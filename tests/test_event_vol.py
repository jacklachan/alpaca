"""The event strategy, and the expiry decision it makes.

The point of these tests is that the expiry choice is driven by the observed
term structure, not by an argument someone made on a Saturday.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from glassbox import config as C
from glassbox.macro import CALENDAR, MEASUREMENT_ET, next_event, sessions_remaining_at_measurement
from glassbox.strategies.event_vol import (ChainLeg, EventVolStrategy, ExpiryQuote,
                                           select_expiry)

NFP = CALENDAR[-1]
THU = datetime(2026, 9, 3, 14, 30, tzinfo=C.ET)
SPOT = Decimal("774")


def q(exp: date, iv: str, spread: str = "0.03") -> ExpiryQuote:
    return ExpiryQuote(expiry=exp, atm_iv=Decimal(iv),
                       atm_straddle_px=Decimal("11"), bid_ask_pct=Decimal(spread))


FLAT = [q(date(2026, 9, 4), "0.132"), q(date(2026, 9, 8), "0.133"),
        q(date(2026, 9, 9), "0.132"), q(date(2026, 9, 11), "0.131")]

EVENT_PREMIUM = [q(date(2026, 9, 4), "0.240"), q(date(2026, 9, 8), "0.185"),
                 q(date(2026, 9, 9), "0.164"), q(date(2026, 9, 11), "0.132")]


# --- the calendar -------------------------------------------------------------

def test_nfp_is_the_only_tier_one_event():
    assert [e.name for e in CALENDAR if e.tier == 1] == ["Employment Situation (Aug)"]
    assert NFP.when == datetime(2026, 9, 4, 8, 30, tzinfo=C.ET)


def test_nfp_lands_one_hour_before_measurement():
    assert (MEASUREMENT_ET - NFP.when).total_seconds() / 3600 == 1.0


def test_labor_day_makes_the_8_sep_expiry_span_a_holiday_weekend():
    """Four calendar days, two sessions. That gap is the discount."""
    assert sessions_remaining_at_measurement(date(2026, 9, 8)) == 2
    assert (date(2026, 9, 8) - MEASUREMENT_ET.date()).days == 4


def test_thursday_afternoon_sees_nfp_as_the_next_catalyst():
    assert next_event(THU, tier=1, within_hours=30).name == NFP.name


# --- expiry selection ---------------------------------------------------------

def test_refuses_zero_dte_which_marks_as_a_stub():
    choice = select_expiry(FLAT, NFP, MEASUREMENT_ET)
    assert choice is not None
    assert choice.expiry != date(2026, 9, 4)


def test_flat_term_structure_takes_the_short_expiry_for_the_holiday_discount():
    """No event premium -> the Labor Day discount dominates, buy more gamma."""
    choice = select_expiry(FLAT, NFP, MEASUREMENT_ET)
    assert choice.expiry == date(2026, 9, 8)
    assert choice.gamma_per_dollar == Decimal("2.5")   # 5 sessions / 2 sessions


def test_event_premium_pushes_us_out_to_the_longer_expiry():
    """The correction: short-dated options carrying event premium get crushed
    once the number is out, which offsets the holiday discount."""
    choice = select_expiry(EVENT_PREMIUM, NFP, MEASUREMENT_ET)
    assert choice.expiry == date(2026, 9, 11)
    assert choice.gamma_per_dollar == Decimal("1")


def test_wide_quotes_disqualify_an_expiry():
    wide = [q(date(2026, 9, 8), "0.132", spread="0.14"), q(date(2026, 9, 11), "0.131")]
    assert select_expiry(wide, NFP, MEASUREMENT_ET).expiry == date(2026, 9, 11)


def test_returns_none_when_nothing_qualifies():
    assert select_expiry([q(date(2026, 9, 4), "0.132")], NFP, MEASUREMENT_ET) is None
    assert select_expiry([], NFP, MEASUREMENT_ET) is None


# --- plan construction --------------------------------------------------------

def chain_for(exp: date) -> dict[date, list[ChainLeg]]:
    legs = []
    for k in range(750, 800, 1):
        strike = Decimal(k)
        for right in ("C", "P"):
            legs.append(ChainLeg(
                symbol=f"SPY{exp:%y%m%d}{right}{int(strike * 1000):08d}",
                strike=strike, right=right,
                ask=Decimal("5.20"), bid=Decimal("5.00")))
    return {exp: legs}


def test_proposes_a_strangle_into_the_catalyst():
    s = EventVolStrategy()
    plans = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.05"),
                      expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                      measurement=MEASUREMENT_ET, remaining_budget=Decimal("18000"))
    assert len(plans) == 1
    p = plans[0]
    assert p.sleeve == "convex" and p.is_event_trade
    assert len(p.option_legs) == 2
    rights = {l.contract.right for l in p.option_legs}
    assert rights == {"C", "P"}
    assert all(l.is_long for l in p.option_legs), "long premium only"
    assert p.max_loss_usd == p.notional_usd, "max loss is exactly the premium"
    # strangle straddles the spot
    strikes = sorted(l.contract.strike for l in p.option_legs)
    assert strikes[0] < SPOT < strikes[1]


def test_stands_down_when_convexity_is_expensive():
    s = EventVolStrategy()
    plans = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.80"),
                      expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                      measurement=MEASUREMENT_ET, remaining_budget=Decimal("18000"))
    assert plans == [], "must not buy gamma it is not being paid to own"


def test_stands_down_when_already_positioned_for_the_event():
    s = EventVolStrategy()
    plans = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.05"),
                      expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                      measurement=MEASUREMENT_ET, remaining_budget=Decimal("18000"),
                      already_positioned_for={NFP.name})
    assert plans == []


def test_sizes_within_the_budget():
    s = EventVolStrategy()
    plans = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.05"),
                      expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                      measurement=MEASUREMENT_ET, remaining_budget=Decimal("4000"))
    assert plans[0].notional_usd <= Decimal("4000")


def test_thesis_and_evidence_are_grounded():
    s = EventVolStrategy()
    p = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.05"),
                  expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                  measurement=MEASUREMENT_ET, remaining_budget=Decimal("18000"))[0]
    assert "Employment Situation" in p.thesis
    joined = " ".join(p.evidence)
    assert "macro_cal:" in joined
    assert "iv_to_rv_ratio" in joined
    assert "expiry_selected" in joined
    assert "event_premium_vol_pts" in joined


# --- the whole path -----------------------------------------------------------

def test_the_event_plan_is_approved_by_the_kernel():
    """The flagship trade must survive its own risk kernel.

    Regression against the original spec, where the $5k/day convex burn cap
    would have refused the trade the entire plan was built around.
    """
    from glassbox.kernel import PortfolioState, RiskKernel

    s = EventVolStrategy()
    plan = s.propose(now=THU, spot=SPOT, iv_vs_rv=Decimal("1.05"),
                     expiry_candidates=FLAT, chain=chain_for(date(2026, 9, 8)),
                     measurement=MEASUREMENT_ET, remaining_budget=Decimal("18000"))[0]

    state = PortfolioState(
        equity=Decimal("100000"), cash=Decimal("40000"),
        core_sleeve_value=Decimal("60000"), core_sleeve_cost_basis=Decimal("60000"),
        convex_premium_today=Decimal("7000"),   # daily cap already spent
        snapshot_price={"SPY": SPOT},
        trading_days_to={date(2026, 9, 8): 4},
        market_open=True, median_order_notional=Decimal("6000"),
        now_et=THU,
    )
    verdict = RiskKernel().review(plan, state)
    assert verdict.approved, verdict.reason
    assert verdict.checks_passed == 13
