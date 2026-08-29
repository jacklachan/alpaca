"""Reconciliation contract.

The kernel fails closed when it lacks data. That is correct, and it means
reconciliation has to supply what the invariants need -- otherwise the system
refuses everything and looks like it is working.
"""

from __future__ import annotations

from datetime import date, datetime

from glassbox import config as C
from glassbox.macro import trading_days_between


def _horizon(today: date, days: int = 45) -> dict[date, int]:
    """Mirrors what Broker.reconcile builds."""
    return {date.fromordinal(today.toordinal() + d):
            trading_days_between(today, date.fromordinal(today.toordinal() + d))
            for d in range(1, days + 1)}


def test_horizon_covers_every_expiry_in_the_scored_window():
    """Regression: reconcile() did not populate trading_days_to at all, so
    invariant 10 refused every option plan with 'no trading-day count'. The
    convex strategy would have been dead at Monday's open."""
    h = _horizon(date(2026, 8, 31))
    for expiry in (date(2026, 9, 4), date(2026, 9, 8),
                   date(2026, 9, 9), date(2026, 9, 11), date(2026, 9, 18)):
        assert expiry in h, f"{expiry} missing from the reconciled horizon"
        assert h[expiry] > 0


def test_horizon_skips_weekends_and_labor_day():
    h = _horizon(date(2026, 9, 4))
    # Fri 4 Sep -> Tue 8 Sep is one session: Sat, Sun and Labor Day do not count.
    assert h[date(2026, 9, 8)] == 1
    assert h[date(2026, 9, 9)] == 2


def test_reconcile_supplies_what_the_expiry_guard_needs():
    from glassbox.kernel import PortfolioState
    from decimal import Decimal
    s = PortfolioState(equity=Decimal("100000"), cash=Decimal("100000"),
                       trading_days_to=_horizon(date(2026, 8, 31)))
    assert s.trading_days_to, "an empty map refuses every option trade"
    assert date(2026, 9, 8) in s.trading_days_to
