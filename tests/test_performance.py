"""Account performance, measured on total equity.

The competition scores total account equity, so these tests pin the summary to
equity endpoints rather than a closed-trade tally. The other half of the
contract is honesty about sample size: a ratio built from a handful of daily
points must announce that it is indicative, not print a confident number.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from glassbox import performance as perf
from glassbox.performance import EquityPoint

START = datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc)


def curve(*equities: str, step_days: int = 1) -> list[EquityPoint]:
    return [
        EquityPoint(at=START + timedelta(days=i * step_days), equity=Decimal(e))
        for i, e in enumerate(equities)
    ]


# -- total equity is the measured quantity -------------------------------------


def test_summary_measures_total_equity_endpoints():
    s = perf.summarize(curve("100000", "101000", "102500"))
    assert s.starting_equity == Decimal("100000")
    assert s.ending_equity == Decimal("102500")
    assert s.absolute_pnl == Decimal("2500")
    assert s.total_return_pct == pytest.approx(2.5)


def test_a_losing_window_reports_a_negative_return():
    s = perf.summarize(curve("100000", "97000"))
    assert s.absolute_pnl == Decimal("-3000")
    assert s.total_return_pct == pytest.approx(-3.0)


def test_no_history_is_reported_as_empty_not_as_zero_performance():
    s = perf.summarize([])
    assert s is perf.EMPTY
    assert s.observations == 0
    assert "no equity history" in " ".join(s.notes)


def test_a_single_observation_cannot_produce_a_return_series():
    s = perf.summarize(curve("100000"))
    assert s.observations == 0
    assert s.total_return_pct == 0.0
    assert any("single equity observation" in n for n in s.notes)


# -- drawdown ------------------------------------------------------------------


def test_max_drawdown_is_peak_to_trough_not_first_to_last():
    # Rises to 110k, falls to 99k, recovers to 105k. The drawdown is 10%,
    # measured from the peak -- not the 5% gain from start to finish.
    s = perf.summarize(curve("100000", "110000", "99000", "105000"))
    assert s.max_drawdown_pct == pytest.approx(-10.0)
    assert s.total_return_pct == pytest.approx(5.0)
    assert s.max_drawdown_at == START + timedelta(days=2)


def test_a_monotonic_curve_has_no_drawdown():
    s = perf.summarize(curve("100000", "101000", "102000"))
    assert s.max_drawdown_pct == 0.0
    assert any("no peak-to-trough decline" in n for n in s.notes)


# -- risk-adjusted ratios ------------------------------------------------------


def test_sharpe_is_zero_when_returns_have_no_dispersion():
    """Every period identical: there is no volatility to divide by, and an
    undefined ratio must not be reported as a large one."""
    flat = [0.001] * 30
    assert perf.sharpe(flat) == 0.0


def test_sharpe_rises_when_the_same_return_comes_with_less_volatility():
    steady = [0.01, 0.011, 0.009, 0.010, 0.011, 0.009] * 5
    choppy = [0.05, -0.03, 0.06, -0.04, 0.05, -0.03] * 5
    assert perf.sharpe(steady) > perf.sharpe(choppy)


def test_sortino_ignores_upside_volatility():
    """Two series, same downside, different upside. Sortino must not punish
    the one that also had big winners; Sharpe does."""
    modest_upside = [-0.01, 0.01, -0.01, 0.01] * 8
    large_upside = [-0.01, 0.08, -0.01, 0.08] * 8
    assert perf.sortino(large_upside) > perf.sortino(modest_upside)


def test_sortino_is_zero_when_nothing_ever_lost():
    """No downside deviation means the ratio is undefined. Reporting infinity
    would look like a spectacular result rather than a missing denominator."""
    assert perf.sortino([0.01, 0.02, 0.015, 0.03] * 8) == 0.0


def test_sharpe_and_sortino_need_at_least_two_observations():
    assert perf.sharpe([0.01]) == 0.0
    assert perf.sortino([0.01]) == 0.0


def test_ratios_annualise_by_the_square_root_of_periods():
    returns = [0.01, -0.005, 0.02, -0.01] * 8
    daily = perf.sharpe(returns, periods_per_year=252)
    assert daily == pytest.approx((perf._mean(returns) / perf._stdev(returns)) * math.sqrt(252))


# -- the honesty constraint ----------------------------------------------------


def test_a_short_window_marks_its_ratios_indicative():
    """Five days of a hackathon cannot support an annualised Sharpe, and the
    summary has to say so rather than print a confident number."""
    s = perf.summarize(curve("100000", "101000", "100500", "102000", "103000"))
    assert s.observations == 4
    assert s.ratios_are_indicative is True
    assert any("indicative only" in n for n in s.notes)


def test_a_long_enough_window_drops_the_caveat():
    values = [str(100000 + i * 137) for i in range(40)]
    s = perf.summarize(curve(*values))
    assert s.observations >= perf.MIN_OBSERVATIONS_FOR_RATIO
    assert s.ratios_are_indicative is False


def test_the_serialised_summary_carries_the_caveat_to_any_consumer():
    s = perf.summarize(curve("100000", "101000", "99000"))
    payload = s.as_dict()
    assert payload["ratios_are_indicative"] is True
    assert payload["notes"], "a consumer could render the ratio without the caveat"
    assert payload["starting_equity"] == "100000"


# -- Alpaca portfolio history --------------------------------------------------


def _history(timestamps, equities):
    return SimpleNamespace(timestamp=timestamps, equity=equities)


def test_portfolio_history_converts_to_equity_points():
    base = int(START.timestamp())
    points = perf.points_from_portfolio_history(
        _history([base, base + 86400], ["100000", "101500"])
    )
    assert [p.equity for p in points] == [Decimal("100000"), Decimal("101500")]
    assert points[0].at == START


def test_null_equity_entries_are_dropped_not_carried_forward():
    """Alpaca returns nulls for periods with no data. Filling one forward
    would invent an observation that every ratio is then computed from."""
    base = int(START.timestamp())
    points = perf.points_from_portfolio_history(
        _history([base, base + 86400, base + 172800], ["100000", None, "101000"])
    )
    assert len(points) == 2
    assert [p.equity for p in points] == [Decimal("100000"), Decimal("101000")]


def test_out_of_order_history_is_sorted_before_it_is_measured():
    base = int(START.timestamp())
    points = perf.points_from_portfolio_history(
        _history([base + 86400, base], ["101000", "100000"])
    )
    assert points[0].equity == Decimal("100000")


def test_unparseable_history_entries_are_skipped():
    base = int(START.timestamp())
    points = perf.points_from_portfolio_history(
        _history([base, "not-a-time", base + 86400], ["100000", "1", "not-a-number"])
    )
    assert [p.equity for p in points] == [Decimal("100000")]


def test_empty_or_missing_history_yields_no_points():
    assert perf.points_from_portfolio_history(SimpleNamespace()) == []
    assert perf.points_from_portfolio_history(_history([], [])) == []


def test_summarize_history_is_one_call_from_alpaca_to_a_summary():
    base = int(START.timestamp())
    s = perf.summarize_history(_history([base, base + 86400], ["100000", "104000"]))
    assert s.absolute_pnl == Decimal("4000")
    assert s.total_return_pct == pytest.approx(4.0)


def test_equity_curve_serialises_for_the_dashboard():
    rows = perf.equity_curve_json(curve("100000", "101000"))
    assert rows == [
        {"at": START.isoformat(), "equity": "100000"},
        {"at": (START + timedelta(days=1)).isoformat(), "equity": "101000"},
    ]
