"""Position management, the kill switch, and the expiry deadline.

The expiry test is the one that would have cost real money: Alpaca stops
accepting options orders at 15:30 ET on expiry day, and the original plan
closed at 15:45.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from glassbox import config as C
from glassbox.journal import Journal
from glassbox.kernel import PortfolioState, Position
from glassbox.manage import ExitOrder, KillSwitch, PositionManager

EXP = date(2026, 9, 8)


def occ(right="C", strike=778, exp=EXP):
    return f"SPY{exp:%y%m%d}{right}{strike * 1000:08d}"


class FakeBroker:
    def __init__(self, fail=False):
        self.closed: list[str] = []
        self.fail = fail

    def close_position(self, symbol):
        if self.fail:
            raise RuntimeError("broker unreachable")
        self.closed.append(symbol)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.jsonl")


@pytest.fixture
def mgr(tmp_path, journal):
    ks = KillSwitch(tmp_path / "kill.json", journal=journal)
    # Exit targets are persisted so they survive a restart; point each test at
    # its own file so they do not leak into one another.
    return PositionManager(FakeBroker(), journal, ks, targets_path=tmp_path / "targets.json")


def state(now: datetime, positions=None, **kw) -> PortfolioState:
    base = dict(
        equity=Decimal("100000"),
        cash=Decimal("40000"),
        core_sleeve_value=Decimal("60000"),
        core_sleeve_cost_basis=Decimal("60000"),
        positions=positions or [],
        now_et=now,
        market_open=True,
        snapshot_price={"SPY": Decimal("769.28"), "QQQ": Decimal("716.91")},
    )
    base.update(kw)
    return PortfolioState(**base)


def opt(symbol=None, qty=10):
    symbol = symbol or occ()
    return Position(
        symbol=symbol,
        instrument="option",
        qty=Decimal(qty),
        market_value=Decimal("3500"),
        underlying="SPY",
        net_delta_shares=Decimal(qty) * 50,
        premium_paid=Decimal("3500"),
    )


def eq(symbol="SPY", qty=20):
    return Position(
        symbol=symbol,
        instrument="equity",
        qty=Decimal(qty),
        market_value=Decimal("15000"),
        net_delta_shares=Decimal(qty),
    )


# --- the expiry deadline ------------------------------------------------------


def test_closes_options_at_1430_on_expiry_day(mgr):
    """14:30 ET, an hour before Alpaca's 15:30 cutoff."""
    now = datetime(2026, 9, 8, 14, 30, tzinfo=C.ET)
    exits = mgr.tick(state(now, [opt()]))
    assert len(exits) == 1
    assert "expiry close-out" in exits[0].reason
    assert exits[0].urgency == "immediate"
    assert occ() in mgr.broker.closed


def test_does_not_close_before_1430(mgr):
    now = datetime(2026, 9, 8, 14, 29, tzinfo=C.ET)
    assert mgr.tick(state(now, [opt()])) == []


def test_close_out_beats_the_broker_cutoff_by_an_hour(mgr):
    """Regression: the original plan closed at 15:45, after the 15:30 cutoff,
    which would have handed the position to auto-exercise and settled T+1 --
    after the account is photographed."""
    assert C.OPTION_FORCE_CLOSE_ET == (14, 30)
    close = C.OPTION_FORCE_CLOSE_ET[0] * 60 + C.OPTION_FORCE_CLOSE_ET[1]
    assert close <= 15 * 60 + 30 - 60


def test_closes_a_position_found_past_its_expiry(mgr):
    now = datetime(2026, 9, 9, 10, 0, tzinfo=C.ET)
    exits = mgr.tick(state(now, [opt()]))
    assert exits and "past expiry" in exits[0].reason


def test_leaves_a_far_dated_option_alone(mgr):
    now = datetime(2026, 9, 3, 14, 45, tzinfo=C.ET)
    assert mgr.tick(state(now, [opt()])) == []


# --- stops, targets, time exits ----------------------------------------------


def test_stop_triggers_an_immediate_exit(mgr):
    mgr.register("SPY", stop=Decimal("780"))
    now = datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)
    exits = mgr.tick(state(now, [eq()]))
    assert exits and "stop hit" in exits[0].reason
    assert exits[0].urgency == "immediate"


def test_target_triggers_an_exit(mgr):
    mgr.register("SPY", target=Decimal("760"))
    now = datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)
    exits = mgr.tick(state(now, [eq()]))
    assert exits and "target hit" in exits[0].reason


def test_time_exit_fires(mgr):
    when = datetime(2026, 9, 4, 9, 30, tzinfo=C.ET)
    mgr.register(occ(), time_exit=when)
    exits = mgr.tick(state(datetime(2026, 9, 4, 9, 31, tzinfo=C.ET), [opt()]))
    assert exits and "time exit" in exits[0].reason


def test_unregistered_position_is_left_alone(mgr):
    now = datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)
    assert mgr.tick(state(now, [eq()])) == []


# --- the kill switch ----------------------------------------------------------


def test_core_drawdown_trips_the_switch(mgr):
    now = datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)
    mgr.tick(
        state(now, core_sleeve_value=Decimal("56000"), core_sleeve_cost_basis=Decimal("60000"))
    )
    assert mgr.kill.tripped
    assert "core sleeve drawdown" in mgr.kill.state()["reason"]


def test_convex_sleeve_going_to_zero_does_not_trip_it(mgr):
    """The design intends this. A switch that fires here is mis-specified."""
    now = datetime(2026, 9, 4, 9, 35, tzinfo=C.ET)
    mgr.tick(
        state(
            now,
            equity=Decimal("89000"),
            core_sleeve_value=Decimal("60000"),
            core_sleeve_cost_basis=Decimal("60000"),
        )
    )
    assert not mgr.kill.tripped


def test_backstop_does_not_fire_on_the_designed_worst_case(mgr):
    """The convex sleeve is permitted to go to zero -- that is a ~50% outcome
    by design, not a failure. A backstop that fires there would latch, flatten
    the sleeve at the bottom, and halt trading for the rest of the week with
    nobody present to re-arm. It must sit BELOW the designed floor.
    """
    now = datetime(2026, 9, 3, 11, 0, tzinfo=C.ET)
    designed_floor = (
        C.STARTING_EQUITY - C.CONVEX_SLEEVE_USD - C.CORE_SLEEVE_USD * C.CORE_DRAWDOWN_KILL_PCT
    )
    # Convex fully spent, core flat: strictly better than the designed floor.
    mgr.tick(
        state(
            now,
            equity=C.STARTING_EQUITY - C.CONVEX_SLEEVE_USD,
            core_sleeve_value=C.CORE_SLEEVE_USD,
            core_sleeve_cost_basis=C.CORE_SLEEVE_USD,
        )
    )
    assert not mgr.kill.tripped, (
        "backstop fired on the strategy doing exactly what it is designed to do"
    )
    assert designed_floor / C.STARTING_EQUITY > (1 - C.PORTFOLIO_DRAWDOWN_KILL_PCT)


def test_portfolio_backstop_trips_on_something_pathological(mgr):
    now = datetime(2026, 9, 3, 11, 0, tzinfo=C.ET)
    beyond = C.STARTING_EQUITY * (1 - C.PORTFOLIO_DRAWDOWN_KILL_PCT) - Decimal("1000")
    mgr.tick(
        state(
            now,
            equity=beyond,
            core_sleeve_value=Decimal("60000"),
            core_sleeve_cost_basis=Decimal("60000"),
        )
    )
    assert mgr.kill.tripped
    assert "backstop" in mgr.kill.state()["reason"]


def test_tripped_switch_flattens_the_convex_sleeve(mgr):
    now = datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)
    mgr.tick(
        state(
            now,
            [opt()],
            core_sleeve_value=Decimal("56000"),
            core_sleeve_cost_basis=Decimal("60000"),
        )
    )
    assert mgr.kill.tripped
    assert occ() in mgr.broker.closed


def test_the_switch_latches_across_restarts(tmp_path, journal):
    path = tmp_path / "kill.json"
    KillSwitch(path, journal=journal).trip("core sleeve drawdown 7.2%")
    assert KillSwitch(path).tripped, "must survive a process restart"


def test_only_a_human_rearms(tmp_path, journal):
    ks = KillSwitch(tmp_path / "kill.json", journal=journal)
    ks.trip("test")
    ks.rearm(who="teammate A", why="cause identified, config corrected")
    assert not ks.tripped
    events = [e["event"] for e in journal.read()]
    assert "KILL_SWITCH_REARMED" in events
    entry = next(e for e in journal.read() if e["event"] == "KILL_SWITCH_REARMED")
    assert entry["actor"] == "human"


def test_unreadable_state_is_treated_as_tripped(tmp_path):
    path = tmp_path / "kill.json"
    path.write_text("{ this is not json")
    assert KillSwitch(path).tripped, "fail closed, never open"


# --- failure handling ---------------------------------------------------------


def test_a_failed_close_is_journalled_not_swallowed(tmp_path, journal):
    ks = KillSwitch(tmp_path / "kill.json", journal=journal)
    m = PositionManager(FakeBroker(fail=True), journal, ks)
    m.tick(state(datetime(2026, 9, 8, 14, 30, tzinfo=C.ET), [opt()]))
    events = [e["event"] for e in journal.read()]
    assert "EXIT_FAILED" in events


def test_every_exit_is_journalled_with_its_reason(mgr, journal):
    mgr.tick(state(datetime(2026, 9, 8, 14, 30, tzinfo=C.ET), [opt()]))
    entry = next(e for e in journal.read() if e["event"] == "EXIT_TRIGGERED")
    assert "expiry close-out" in entry["payload"]["reason"]
    ok, why = journal.verify()
    assert ok, why


def test_corrupt_exit_targets_stop_manager_construction(tmp_path, journal):
    targets = tmp_path / "targets.json"
    targets.write_text('{"SPY": {"stop": ["not", "decimal"]}}', encoding="utf-8")
    kill = KillSwitch(tmp_path / "kill.json", journal)

    with pytest.raises(RuntimeError, match="targets"):
        PositionManager(FakeBroker(), journal, kill, targets_path=targets)


# -- ledger-backed exact exits (Task E) ---------------------------------------


class ExitBroker:
    """Records submits and confirms them terminal. Never closes symbol-wide."""

    def __init__(self, fill: str = "full", terminal_filled: str | None = None):
        self.submitted: list[dict] = []
        self.closed: list[str] = []
        self.confirmed: list[str] = []
        self.fill = fill
        # Alpaca reports filled_qty cumulatively per order, so the terminal
        # read is the total for that order -- never less than an earlier read.
        self.terminal_filled = terminal_filled if terminal_filled is not None else fill

    def close_position(self, symbol):  # pragma: no cover - must never run here
        self.closed.append(symbol)

    def submit(self, *, symbol, qty, side, client_order_id, limit_price=None, instrument="equity"):
        self.submitted.append(
            {"symbol": symbol, "qty": Decimal(str(qty)), "side": side, "coid": client_order_id}
        )
        filled = Decimal(str(qty)) if self.fill == "full" else Decimal(self.fill)
        return SimpleNamespace(
            id=f"broker-{len(self.submitted)}",
            client_order_id=client_order_id,
            symbol=symbol,
            status="filled" if filled >= Decimal(str(qty)) else "partially_filled",
            filled_qty=filled,
            filled_avg_price="4.00",
        )

    def cancel_and_confirm(self, order_id, client_order_id, **kw):
        self.confirmed.append(client_order_id)
        return SimpleNamespace(
            id=order_id,
            client_order_id=client_order_id,
            status="canceled",
            filled_qty=Decimal(self.terminal_filled),
            filled_avg_price="4.00",
        )


def _ledger_manager(tmp_path, journal, broker, *, owned: str | None = "10"):
    from glassbox.position_ledger import PositionLedger

    book = PositionLedger(account_id="PA-1", environment="scored")
    if owned is not None:
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=occ(),
            client_order_id="gbx-entry-1",
            filled_qty=Decimal(owned),
            side="buy",
        )
    ks = KillSwitch(tmp_path / "kill.json", journal=journal)
    manager = PositionManager(
        broker,
        journal,
        ks,
        targets_path=tmp_path / "targets.json",
        ledger=book,
        ledger_path=tmp_path / "ledger.json",
    )
    return manager, book


def test_exit_sells_the_exact_owned_quantity_and_never_closes_symbol_wide(tmp_path, journal):
    broker = ExitBroker()
    manager, book = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.closed == [], "a symbol-wide close ran on the scored path"
    assert len(broker.submitted) == 1
    submitted = broker.submitted[0]
    assert submitted["qty"] == Decimal(10)
    assert submitted["side"] == "sell"
    assert submitted["coid"].startswith("gbx-x-")
    assert book.entries[occ()].signed_qty == Decimal(0)


def test_exit_intent_is_durable_before_submit(tmp_path, journal):
    """The exit client id must be on disk before the order is sent, so a crash
    in between finds the same id instead of minting a second order."""
    ledger_path = tmp_path / "ledger.json"
    seen: dict[str, object] = {}

    class CrashingBroker(ExitBroker):
        def submit(self, **kw):
            from glassbox.position_ledger import PositionLedger

            # What is on disk at the moment of the mutation?
            saved = PositionLedger.load(ledger_path, account_id="PA-1", environment="scored")
            seen["exit_coids"] = saved.entries[occ()].exit_coids
            return super().submit(**kw)

    broker = CrashingBroker()
    manager, _ = _ledger_manager(tmp_path, journal, broker)
    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert seen["exit_coids"], "the exit intent was not durable before submit"
    assert seen["exit_coids"] == (broker.submitted[0]["coid"],)


def test_exit_refuses_exposure_the_strategy_does_not_own(tmp_path, journal):
    broker = ExitBroker()
    manager, _ = _ledger_manager(tmp_path, journal, broker, owned=None)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.submitted == []
    assert broker.closed == [], "foreign exposure was liquidated"
    events = [e["event"] for e in journal.read()]
    assert "EXIT_REFUSED_UNOWNED" in events


def test_partial_exit_cancels_residual_and_preserves_remaining_target(tmp_path, journal):
    """Six of ten sold, residual cancelled terminal. Four remain ours, and the
    next tick must be free to exit them."""
    broker = ExitBroker(fill="6", terminal_filled="6")
    manager, book = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.confirmed, "the residual was never confirmed terminal"
    assert book.entries[occ()].signed_qty == Decimal(4)
    assert book.entries[occ()].exit_qty == Decimal(4)
    assert occ() not in manager._exits_sent, "a partial exit blocked the retry"

    terminal = [e for e in journal.read() if e["event"] == "EXIT_ORDER_TERMINAL"]
    assert terminal[-1]["payload"]["remaining_qty"] == "4"


def test_late_exit_fill_after_cancel_reconciles_to_exact_flat(tmp_path, journal):
    """Six filled on the working order, four more on the way out during the
    cancel, so the terminal read reports ten. The ledger must land at zero."""
    broker = ExitBroker(fill="6", terminal_filled="10")
    manager, book = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert book.entries[occ()].signed_qty == Decimal(0)
    assert book.is_flat(occ(), venue_qty=Decimal(0), exit_orders_terminal=True) is True


def test_second_exit_attempt_uses_a_distinct_deterministic_id(tmp_path, journal):
    broker = ExitBroker(fill="6", terminal_filled="6")
    manager, _ = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))
    manager._close(ExitOrder(symbol=occ(), qty=Decimal(4), reason="target"))

    first, second = broker.submitted[0]["coid"], broker.submitted[1]["coid"]
    assert first != second
    assert broker.submitted[1]["qty"] == Decimal(4)
