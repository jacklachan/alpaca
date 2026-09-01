"""Flattening an option whose mark cannot be trusted at the snapshot.

The account is valued at a known instant, off the indicative feed. A wide or
absent quote at that instant is not evidence of anything, and cash has no
marking ambiguity. These tests pin the narrowness of the rule as much as the
rule: it must never open risk, never touch a contract it can price, and never
fire outside the window.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from glassbox import config as C
from glassbox.journal import Journal
from glassbox.kernel import PortfolioState, Position
from glassbox.macro import MEASUREMENT_ET
from glassbox.manage import KillSwitch, PositionManager

EXP = date(2026, 9, 11)
SYM = f"SPY{EXP:%y%m%d}C00780000"


class FakeBroker:
    def __init__(self):
        self.closed: list[str] = []

    def close_position(self, symbol):
        self.closed.append(symbol)


@pytest.fixture
def mgr(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    return PositionManager(
        FakeBroker(),
        j,
        KillSwitch(tmp_path / "kill.json", journal=j),
        targets_path=tmp_path / "targets.json",
        exit_state_path=tmp_path / "exits.json",
    )


def opt(qty=5):
    return Position(
        symbol=SYM,
        instrument="option",
        qty=Decimal(qty),
        market_value=Decimal("2500"),
        underlying="SPY",
        net_delta_shares=Decimal(qty) * 50,
        premium_paid=Decimal("2500"),
    )


def state(minutes_before: float, spread: str | None, positions=None) -> PortfolioState:
    now = MEASUREMENT_ET - timedelta(minutes=minutes_before)
    return PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("40000"),
        positions=positions if positions is not None else [opt()],
        now_et=now,
        market_open=True,
        snapshot_price={"SPY": Decimal("769")},
        option_quote_spread={} if spread is None else {SYM: Decimal(spread)},
    )


# --- it fires when the mark is unusable ---------------------------------------


def test_flattens_an_option_quoting_too_wide_to_mark(mgr):
    exits = mgr.tick(state(20, "0.30"))
    assert len(exits) == 1
    assert "the mark is not evidence" in exits[0].reason
    assert exits[0].urgency == "immediate"
    assert SYM in mgr.broker.closed


def test_an_unquoted_contract_is_the_worst_case_not_an_exemption(mgr):
    """No two-sided quote is precisely what must not reach the snapshot."""
    exits = mgr.tick(state(20, None))
    assert len(exits) == 1
    assert "no two-sided quote" in exits[0].reason
    assert SYM in mgr.broker.closed


# --- it stays out of the way otherwise ----------------------------------------


def test_leaves_a_tightly_quoted_position_alone(mgr):
    """A mark we can defend is worth more than cash: this is not a flatten-all."""
    assert mgr.tick(state(20, "0.03")) == []
    assert mgr.broker.closed == []


def test_does_not_fire_outside_the_window(mgr):
    assert mgr.tick(state(C.MEASUREMENT_FLATTEN_MINUTES + 30, "0.30")) == []
    assert mgr.broker.closed == []


def test_does_not_fire_after_measurement_has_passed(mgr):
    assert mgr.tick(state(-10, "0.30")) == []
    assert mgr.broker.closed == []


def test_ignores_non_option_positions(mgr):
    equity = Position(
        symbol="SPY", instrument="equity", qty=Decimal(20), market_value=Decimal("15000")
    )
    assert mgr.tick(state(20, "0.30", positions=[equity])) == []
    assert mgr.broker.closed == []


def test_the_threshold_is_looser_than_the_entry_gate(mgr):
    """Refusing to ENTER on a 5.5% spread is prudence. Flattening a working
    position needs the quote to be genuinely unusable, not merely worse than we
    would have chosen, or the rule closes good positions at the worst moment."""
    assert C.MEASUREMENT_MAX_MARK_SPREAD_PCT > C.MAX_ATM_SPREAD_PCT
    assert mgr.tick(state(20, str(float(C.MAX_ATM_SPREAD_PCT) + 0.01))) == []


# --- the record ----------------------------------------------------------------


def test_the_exit_is_journalled_with_the_measured_spread(mgr):
    mgr.tick(state(20, "0.30"))
    entry = next(e for e in mgr.journal.read() if e["event"] == "EXIT_TRIGGERED")
    assert "30.0%" in entry["payload"]["reason"]
    ok, why = mgr.journal.verify()
    assert ok, why
