"""Deterministic option-surface gates.

The cases that matter are the ones where a trade looks fine by name and is
wrong by exposure: a "strangle" that is net short gamma, convexity bought at a
volatility that will collapse, and decay that outruns the event.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from glassbox import greeks as G
from glassbox.greeks import LegGreeks, assess_long_convexity

CALL = "SPY260904C00780000"
PUT = "SPY260904P00760000"


def leg(symbol: str, side: str = "buy", qty: int = 1):
    return SimpleNamespace(symbol=symbol, side=side, qty=qty)


def surface(
    symbol: str,
    *,
    delta="0.30",
    gamma="0.02",
    theta="-0.15",
    vega="0.40",
    iv="0.40",
) -> LegGreeks:
    return LegGreeks(
        symbol=symbol,
        delta=Decimal(delta),
        gamma=Decimal(gamma),
        theta=Decimal(theta),
        vega=Decimal(vega),
        implied_volatility=Decimal(iv),
    )


def healthy() -> dict[str, LegGreeks]:
    return {CALL: surface(CALL), PUT: surface(PUT, delta="-0.28")}


def assess(greeks, *, premium="600", spot="770", contracts="1", **kw):
    return assess_long_convexity(
        [leg(CALL), leg(PUT)],
        greeks,
        premium_paid=Decimal(premium),
        spot=Decimal(spot),
        contracts=Decimal(contracts),
        **kw,
    )


# -- aggregation ---------------------------------------------------------------


def test_aggregate_scales_by_contracts_and_signs_by_side():
    position = G.aggregate([leg(CALL, qty=2)], {CALL: surface(CALL, delta="0.5")})
    assert position.delta == Decimal("100.0")  # 0.5 * 2 * 100

    short = G.aggregate([leg(CALL, side="sell", qty=2)], {CALL: surface(CALL, delta="0.5")})
    assert short.delta == Decimal("-100.0")


def test_an_unpriced_leg_makes_the_whole_aggregate_none():
    """A partial aggregate understates exposure exactly where it matters."""
    assert G.aggregate([leg(CALL), leg(PUT)], {CALL: surface(CALL)}) is None


def test_from_snapshot_returns_none_rather_than_zeros_when_greeks_are_missing():
    """Zero delta is a meaningful value and must never stand in for absent."""
    assert LegGreeks.from_snapshot(CALL, SimpleNamespace(greeks=None)) is None
    partial = SimpleNamespace(
        greeks=SimpleNamespace(delta="0.3", gamma=None, theta="-0.1", vega="0.4"),
        implied_volatility="0.4",
    )
    assert LegGreeks.from_snapshot(CALL, partial) is None


def test_from_snapshot_reads_a_complete_surface():
    snapshot = SimpleNamespace(
        greeks=SimpleNamespace(delta="0.31", gamma="0.02", theta="-0.12", vega="0.44", rho="0.01"),
        implied_volatility="0.38",
    )
    parsed = LegGreeks.from_snapshot(CALL, snapshot)
    assert parsed.delta == Decimal("0.31")
    assert parsed.implied_volatility == Decimal("0.38")


# -- the trade must be the trade it claims to be -------------------------------


def test_a_healthy_long_strangle_is_approved():
    verdict = assess(healthy())
    assert verdict.approved, verdict.reason
    assert verdict.position.is_long_gamma and verdict.position.is_long_vega


def test_a_strangle_that_is_net_short_gamma_is_refused():
    """Mislabelled by name, wrong by exposure. This is the check that catches
    a sign error or a leg that never filled."""
    broken = {CALL: surface(CALL, gamma="-0.05"), PUT: surface(PUT, delta="-0.28", gamma="-0.05")}
    verdict = assess(broken)
    assert not verdict.approved
    assert "not net long gamma" in verdict.reason


def test_a_position_that_is_net_short_vega_is_refused():
    flat = {CALL: surface(CALL, vega="-0.4"), PUT: surface(PUT, delta="-0.28", vega="-0.4")}
    verdict = assess(flat)
    assert not verdict.approved
    assert "not net long vega" in verdict.reason


def test_a_leg_that_contributes_nothing_is_refused():
    """Two legs that behave like one is a single-sided bet in disguise."""
    lopsided = {CALL: surface(CALL), PUT: surface(PUT, delta="0.001")}
    verdict = assess(lopsided)
    assert not verdict.approved
    assert "contributes almost nothing" in verdict.reason


# -- the convexity must be affordable ------------------------------------------


def test_convexity_bought_at_rich_implied_volatility_is_refused():
    """Right on direction, wrong on price: the post-event IV collapse is how
    a correct call still loses money."""
    rich = {CALL: surface(CALL, iv="1.20"), PUT: surface(PUT, delta="-0.28", iv="1.20")}
    verdict = assess(rich)
    assert not verdict.approved
    assert "implied volatility" in verdict.reason
    assert "priced rich" in verdict.reason


def test_decay_that_outruns_the_event_is_refused():
    """Theta is the rent. If it eats the premium in days, the move has to
    arrive almost immediately to pay for it."""
    burning = {
        CALL: surface(CALL, theta="-1.50"),
        PUT: surface(PUT, delta="-0.28", theta="-1.50"),
    }
    verdict = assess(burning, premium="600")
    assert not verdict.approved
    assert "theta burn" in verdict.reason


def test_a_breakeven_further_than_the_event_can_deliver_is_refused():
    verdict = assess(healthy(), premium="6000", spot="770", contracts="1")
    assert not verdict.approved
    assert "breakeven move" in verdict.reason


def test_thresholds_are_injectable_so_a_policy_change_is_explicit():
    strict = assess(healthy(), max_daily_theta_burn=Decimal("0.0001"))
    assert not strict.approved
    assert "theta burn" in strict.reason


# -- missing data means abstain, never guess -----------------------------------


def test_a_missing_surface_abstains_rather_than_assuming():
    verdict = assess({CALL: surface(CALL)})
    assert not verdict.approved
    assert "surface unavailable" in verdict.reason
    assert any("abstained" in e for e in verdict.evidence)


def test_the_verdict_carries_quotable_evidence():
    verdict = assess(healthy())
    body = verdict.as_dict()
    assert body["approved"] is True
    assert body["position"]["gamma"]
    assert any("net delta" in e for e in body["evidence"])
    assert any("theta burn" in e for e in body["evidence"])


# -- arithmetic ----------------------------------------------------------------


def test_theta_burn_is_a_positive_fraction_of_premium():
    position = G.PositionGreeks(theta=Decimal("-60"))
    assert G.daily_theta_burn_pct(position, Decimal("600")) == Decimal("0.1")


def test_theta_burn_is_zero_when_no_premium_was_paid():
    assert G.daily_theta_burn_pct(G.PositionGreeks(theta=Decimal("-60")), Decimal(0)) == 0


def test_breakeven_move_is_premium_per_share_over_spot():
    move = G.breakeven_move_pct(
        premium_paid=Decimal("770"), spot=Decimal("770"), contracts=Decimal(1)
    )
    assert move == Decimal("0.01")


@pytest.mark.parametrize(("spot", "contracts"), [("0", "1"), ("770", "0")])
def test_breakeven_move_is_undefined_without_a_spot_or_a_size(spot: str, contracts: str):
    assert (
        G.breakeven_move_pct(
            premium_paid=Decimal("600"), spot=Decimal(spot), contracts=Decimal(contracts)
        )
        is None
    )
