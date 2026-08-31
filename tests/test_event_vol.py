"""The event strategy, and the expiry decision it makes.

The point of these tests is that the expiry choice is driven by the observed
term structure, not by an argument someone made on a Saturday.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from glassbox import config as C
from glassbox.candidates import (
    CANDIDATE_SCHEMA_VERSION,
    LIMIT_PRICE_RULE_VERSION,
    OptionQuoteSnapshot,
    derive_limit_price,
)
from glassbox.macro import CALENDAR, MEASUREMENT_ET, next_event, sessions_remaining_at_measurement
from glassbox.strategies.event_vol import ChainLeg, EventVolStrategy, ExpiryQuote, select_expiry

# The measurement is EOD Thu 3 Sep, so the August payrolls print (Fri 4 Sep
# 08:30) falls OUTSIDE the scored window. It is kept in the calendar because it
# is real, and excluded from trading because it pays out after we are measured.
NFP = CALENDAR[-1]

# The catalyst the strategy actually selects from the trade window, rather than
# one hand-picked here -- so the test moves with the calendar instead of
# encoding a second, drifting copy of the selection rule.

# Wednesday afternoon: the trade window. By Thursday 14:30 every in-window
# catalyst has already printed, so there is nothing left to position for.
WED = datetime(2026, 9, 2, 14, 30, tzinfo=C.ET)
THU_LATE = datetime(2026, 9, 3, 14, 30, tzinfo=C.ET)
CATALYST = next_event(WED, tier=C.EVENT_MIN_TIER, within_hours=30)
SPOT = Decimal("774")


def q(exp: date, iv: str, spread: str = "0.03") -> ExpiryQuote:
    return ExpiryQuote(
        expiry=exp, atm_iv=Decimal(iv), atm_straddle_px=Decimal("11"), bid_ask_pct=Decimal(spread)
    )


# Re-based on the EOD Thu 3 Sep measurement:
#   3 Sep -> 1 session (below the minimum, refused)
#   4 Sep -> 2,  8 Sep -> 3,  9 Sep -> 4,  11 Sep -> 6
FLAT = [
    q(date(2026, 9, 3), "0.132"),
    q(date(2026, 9, 4), "0.133"),
    q(date(2026, 9, 8), "0.133"),
    q(date(2026, 9, 11), "0.131"),
]

EVENT_PREMIUM = [
    q(date(2026, 9, 3), "0.240"),
    q(date(2026, 9, 4), "0.240"),
    q(date(2026, 9, 8), "0.185"),
    q(date(2026, 9, 11), "0.132"),
]


# --- the calendar -------------------------------------------------------------


def test_nfp_is_the_only_tier_one_event():
    assert [e.name for e in CALENDAR if e.tier == 1] == ["Employment Situation (Aug)"]
    assert NFP.when == datetime(2026, 9, 4, 8, 30, tzinfo=C.ET)


def test_payrolls_falls_OUTSIDE_the_scored_window():
    """The correction that reshaped this strategy.

    We built the original trade around payrolls landing 60 minutes before a
    Friday 09:30 measurement. Alpaca's guidelines put the measurement at EOD
    Thursday 3 Sep, which puts payrolls ~16.5 hours the wrong side of it.
    """
    assert NFP.when > MEASUREMENT_ET
    assert (NFP.when - MEASUREMENT_ET).total_seconds() / 3600 > 16


def test_a_post_measurement_catalyst_is_never_selected():
    """Regression guard. Buying convexity for payrolls would be paying for a
    payoff that lands after the account has already been photographed."""
    from glassbox.macro import post_measurement_events

    assert NFP in post_measurement_events()
    # Even standing on Thursday with payrolls well inside the lookahead window.
    assert next_event(THU_LATE, tier=1, within_hours=48) is None


def test_labor_day_makes_the_8_sep_expiry_span_a_holiday_weekend():
    """Five calendar days, three sessions. That gap is the discount."""
    assert sessions_remaining_at_measurement(date(2026, 9, 8)) == 2
    assert (date(2026, 9, 8) - MEASUREMENT_ET.date()).days == 5


def test_wednesday_sees_the_thursday_catalysts():
    assert next_event(WED, tier=2, within_hours=30) is not None


def test_thursday_afternoon_has_nothing_left_to_trade():
    """Every in-window catalyst has printed by 14:30 Thursday. Standing down is
    the correct behaviour, not a failure."""
    assert next_event(THU_LATE, tier=2, within_hours=48) is None


# --- expiry selection ---------------------------------------------------------


def test_refuses_zero_dte_which_marks_as_a_stub():
    """3 Sep is the 0DTE now -- it expires on the measurement day itself."""
    choice = select_expiry(FLAT, CATALYST, MEASUREMENT_ET)
    assert choice is not None
    assert choice.expiry != date(2026, 9, 3)


def test_flat_term_structure_takes_the_shortest_surviving_expiry():
    """No event premium -> buy the most gamma per dollar that still marks off a
    real quote at measurement. Against an EOD Thu 3 Sep snapshot that is the
    4 Sep expiry: two sessions left, so three times the convexity per dollar of
    the 11 Sep contract a team without this analysis would default to."""
    choice = select_expiry(FLAT, CATALYST, MEASUREMENT_ET)
    assert choice.expiry == date(2026, 9, 8)
    assert choice.gamma_per_dollar == Decimal("2.5")  # 5 sessions / 2 sessions


def test_event_premium_pushes_us_out_to_the_longer_expiry():
    """The correction: short-dated options carrying event premium get crushed
    once the number is out, which offsets the holiday discount."""
    choice = select_expiry(EVENT_PREMIUM, CATALYST, MEASUREMENT_ET)
    assert choice.expiry == date(2026, 9, 11)
    assert choice.gamma_per_dollar == Decimal("1")


def test_wide_quotes_disqualify_an_expiry():
    wide = [q(date(2026, 9, 4), "0.132", spread="0.14"), q(date(2026, 9, 11), "0.131")]
    assert select_expiry(wide, CATALYST, MEASUREMENT_ET).expiry == date(2026, 9, 11)


def test_returns_none_when_nothing_qualifies():
    assert select_expiry([q(date(2026, 9, 3), "0.132")], CATALYST, MEASUREMENT_ET) is None
    assert select_expiry([], CATALYST, MEASUREMENT_ET) is None


# --- plan construction --------------------------------------------------------


def chain_for(exp: date, *, verified: bool = True) -> dict[date, list[ChainLeg]]:
    legs = []
    observed_at = datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc)
    for k in range(750, 800, 1):
        strike = Decimal(k)
        for right in ("C", "P"):
            symbol = f"SPY{exp:%y%m%d}{right}{int(strike * 1000):08d}"
            snapshot = (
                OptionQuoteSnapshot.capture(
                    contract_id=f"contract-{symbol}",
                    symbol=symbol,
                    status="active",
                    tradable=True,
                    quote_source="alpaca_option_chain",
                    feed="indicative",
                    venue_timestamp=observed_at - timedelta(seconds=4),
                    observed_at=observed_at,
                    bid=Decimal("5.00"),
                    ask=Decimal("5.20"),
                    max_age_seconds=Decimal("30"),
                    max_spread_pct=Decimal("0.055"),
                )
                if verified
                else None
            )
            legs.append(
                ChainLeg(
                    symbol=symbol,
                    strike=strike,
                    right=right,
                    ask=Decimal("5.20"),
                    bid=Decimal("5.00"),
                    quote_snapshot=snapshot,
                )
            )
    return {exp: legs}


def _propose(option_surface=None, journal=None):
    """Propose through the real strategy, optionally with a surface source.

    Mirrors how MarketData exposes the gateway, so the wiring under test is
    the wiring that runs.
    """
    from types import SimpleNamespace

    data = None
    if option_surface is not None or journal is not None:
        data = SimpleNamespace(
            options=SimpleNamespace(option_surface=option_surface) if option_surface else None,
            journal=journal,
        )
    strategy = EventVolStrategy(data=data)
    return strategy.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )


def test_proposes_a_strangle_into_the_catalyst():
    s = EventVolStrategy()
    plans = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )
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


def test_same_event_opportunity_has_the_same_plan_id_after_restart():
    kwargs = dict(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )

    first = EventVolStrategy().propose(**kwargs)[0]
    restarted = EventVolStrategy().propose(**kwargs)[0]

    assert first.plan_id == restarted.plan_id


def test_candidate_requires_verified_chain_quote_provenance():
    plans = EventVolStrategy().propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8), verified=False),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )

    assert plans == []


def test_candidate_carries_quote_provenance_and_versioned_decimal_limits():
    plan = EventVolStrategy().propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )[0]

    assert plan.candidate_schema_version == CANDIDATE_SCHEMA_VERSION
    assert plan.limit_price_rule_version == LIMIT_PRICE_RULE_VERSION
    assert tuple(quote.symbol for quote in plan.quote_snapshots) == tuple(
        leg.symbol for leg in plan.option_legs
    )
    assert tuple(leg.limit_price for leg in plan.option_legs) == tuple(
        derive_limit_price(quote, tolerance=C.LIMIT_TOLERANCE) for quote in plan.quote_snapshots
    )
    assert len(plan.content_hash) == 64


def test_priced_candidate_is_immutable():
    plan = EventVolStrategy().propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )[0]

    with pytest.raises(ValidationError):
        plan.symbol = "QQQ"


def test_stands_down_when_convexity_is_expensive():
    s = EventVolStrategy()
    plans = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.80"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )
    assert plans == [], "must not buy gamma it is not being paid to own"


def test_stands_down_when_already_positioned_for_the_event():
    s = EventVolStrategy()
    plans = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
        already_positioned_for={CATALYST.name},
    )
    assert plans == []


def test_sizes_within_the_budget():
    s = EventVolStrategy()
    plans = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("4000"),
    )
    assert plans[0].notional_usd <= Decimal("4000")


def test_thesis_and_evidence_are_grounded():
    s = EventVolStrategy()
    p = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )[0]
    assert CATALYST.name in p.thesis
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
    plan = s.propose(
        now=WED,
        spot=SPOT,
        iv_vs_rv=Decimal("1.05"),
        expiry_candidates=FLAT,
        chain=chain_for(date(2026, 9, 8)),
        measurement=MEASUREMENT_ET,
        remaining_budget=Decimal("18000"),
    )[0]

    state = PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("40000"),
        core_sleeve_value=Decimal("60000"),
        core_sleeve_cost_basis=Decimal("60000"),
        convex_premium_today=Decimal("7000"),  # daily cap already spent
        snapshot_price={"SPY": SPOT},
        trading_days_to={date(2026, 9, 4): 4, date(2026, 9, 8): 6, date(2026, 9, 11): 9},
        market_open=True,
        median_order_notional=Decimal("6000"),
        now_et=WED,
    )
    verdict = RiskKernel().review(plan, state)
    assert verdict.approved, verdict.reason
    assert verdict.checks_passed == 13


# -- option-surface gating -----------------------------------------------------


def test_a_candidate_records_that_no_surface_was_published(monkeypatch):
    """Missing Greeks must not silently look like healthy Greeks."""
    plans = _propose()
    assert plans, "fixture produced no candidate"
    assert any("option_surface=unavailable" in e for e in plans[0].evidence)


def test_a_surface_that_is_net_short_gamma_refuses_the_candidate(monkeypatch):
    """The venue says this 'strangle' is short gamma. It is not the trade the
    thesis describes, so it is refused before an order exists."""
    from decimal import Decimal as D

    from glassbox.greeks import LegGreeks

    def bad_surface(underlying, symbols):
        return {
            s: LegGreeks(
                symbol=s,
                delta=D("0.3") if "C" in s[-9:] else D("-0.3"),
                gamma=D("-0.05"),
                theta=D("-0.10"),
                vega=D("0.40"),
                implied_volatility=D("0.40"),
            )
            for s in symbols
        }

    plans = _propose(option_surface=bad_surface)
    assert plans == [], "a short-gamma strangle was proposed"


def test_a_healthy_surface_is_recorded_as_evidence():
    from decimal import Decimal as D

    from glassbox.greeks import LegGreeks

    def good_surface(underlying, symbols):
        return {
            s: LegGreeks(
                symbol=s,
                delta=D("0.30") if "C" in s[-9:] else D("-0.28"),
                gamma=D("0.02"),
                theta=D("-0.05"),
                vega=D("0.40"),
                implied_volatility=D("0.35"),
            )
            for s in symbols
        }

    plans = _propose(option_surface=good_surface)
    assert plans, "a healthy surface blocked the candidate"
    assert any(e.startswith("surface: net delta") for e in plans[0].evidence)


def test_a_surface_lookup_that_raises_does_not_block_trading():
    """A data outage must cost evidence, not the whole strategy."""

    def broken(underlying, symbols):
        raise RuntimeError("option chain unavailable")

    plans = _propose(option_surface=broken)
    assert plans, "a surface outage stopped the agent trading"
    assert any("option_surface=unavailable (RuntimeError)" in e for e in plans[0].evidence)


# --- regression: an EOD measurement spends the measurement day ----------------


def test_the_measurement_day_is_not_remaining_life_at_an_eod_snapshot():
    """The account is valued at Thursday's close, so Thursday's session is
    already spent. Counting it inclusively -- correct for the old Friday 09:30
    measurement -- overstated every contract by one and let a one-session
    contract pass OPTION_MIN_DTE_AT_MEASUREMENT, which requires two."""
    assert sessions_remaining_at_measurement(date(2026, 9, 3)) == 0
    assert sessions_remaining_at_measurement(date(2026, 9, 4)) == 1
    assert sessions_remaining_at_measurement(date(2026, 9, 8)) == 2


def test_the_same_day_counts_again_when_measured_at_the_open():
    """The rule is time-aware, not a blanket decrement."""
    at_open = datetime(2026, 9, 3, 9, 30, tzinfo=C.ET)
    assert sessions_remaining_at_measurement(date(2026, 9, 4), at_open) == 2


def test_a_one_session_expiry_is_never_selected():
    """4 Sep has a single session left at an EOD Thu 3 Sep measurement. It marks
    off a near-expiry stub and must be refused however cheap it looks."""
    tempting = [
        q(date(2026, 9, 4), "0.060"),
        q(date(2026, 9, 8), "0.133"),
        q(date(2026, 9, 11), "0.131"),
    ]
    choice = select_expiry(tempting, NFP, MEASUREMENT_ET)
    assert choice is not None
    assert choice.expiry != date(2026, 9, 4)
