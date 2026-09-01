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
    return PositionManager(
        FakeBroker(),
        journal,
        ks,
        targets_path=tmp_path / "targets.json",
        exit_state_path=tmp_path / "exit_state.json",
    )


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
        self.orders: dict[str, object] = {}
        self.venue_qty: dict[str, Decimal] = {}

    def close_position(self, symbol):  # pragma: no cover - must never run here
        self.closed.append(symbol)

    def submit(self, *, symbol, qty, side, client_order_id, limit_price=None, instrument="equity"):
        self.submitted.append(
            {"symbol": symbol, "qty": Decimal(str(qty)), "side": side, "coid": client_order_id}
        )
        filled = Decimal(str(qty)) if self.fill == "full" else Decimal(self.fill)
        order = SimpleNamespace(
            id=f"broker-{len(self.submitted)}",
            client_order_id=client_order_id,
            symbol=symbol,
            status="filled" if filled >= Decimal(str(qty)) else "partially_filled",
            filled_qty=filled,
            filled_avg_price="4.00",
        )
        self.orders[client_order_id] = order
        signed = filled if side == "buy" else -filled
        self.venue_qty[symbol] = self.venue_qty.get(symbol, Decimal(0)) + signed
        return order

    def cancel_and_confirm(self, order_id, client_order_id, **kw):
        self.confirmed.append(client_order_id)
        order = self.orders[client_order_id]
        final_filled = Decimal(self.terminal_filled)
        prior = Decimal(str(order.filled_qty))
        self.venue_qty[order.symbol] = self.venue_qty.get(order.symbol, Decimal(0)) - (
            final_filled - prior
        )
        final = SimpleNamespace(
            id=order_id,
            client_order_id=client_order_id,
            symbol=order.symbol,
            status="canceled",
            filled_qty=final_filled,
            filled_avg_price="4.00",
        )
        self.orders[client_order_id] = final
        return final

    def get_order_by_coid(self, client_order_id):
        return self.orders.get(client_order_id)

    def positions(self):
        return [
            SimpleNamespace(symbol=symbol, qty=qty) for symbol, qty in self.venue_qty.items() if qty
        ]

    def open_orders(self):
        return [
            order for order in self.orders.values() if order.status not in {"filled", "canceled"}
        ]


def _ledger_manager(tmp_path, journal, broker, *, owned: str | None = "10"):
    from glassbox.position_ledger import PositionLedger

    book = PositionLedger(account_id="PA-1", environment="scored")
    if owned is not None:
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=occ(),
            client_order_id="gbx-entry-1",
            filled_qty=Decimal(owned),
            order_qty=Decimal(owned),
            side="buy",
        )
        if hasattr(broker, "venue_qty"):
            broker.venue_qty[occ()] = Decimal(owned)
    ks = KillSwitch(tmp_path / "kill.json", journal=journal)
    manager = PositionManager(
        broker,
        journal,
        ks,
        targets_path=tmp_path / "targets.json",
        ledger=book,
        ledger_path=tmp_path / "ledger.json",
        exit_state_path=tmp_path / "exit_state.json",
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


def test_ambiguous_accepted_exit_is_adopted_by_original_id(tmp_path, journal):
    """A lost submit response must never mint or submit a second exit."""

    class AcceptedThenTimeout(ExitBroker):
        def __init__(self):
            super().__init__()
            self.timed_out = False

        def submit(self, **kwargs):
            order = super().submit(**kwargs)
            if not self.timed_out:
                self.timed_out = True
                raise TimeoutError("response lost after venue acceptance")
            return order

    broker = AcceptedThenTimeout()
    manager, book = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert len(broker.submitted) == 1
    assert book.entries[occ()].signed_qty == Decimal(0)
    events = [entry["event"] for entry in journal.read()]
    assert "ORDER_SUBMIT_RECONCILED" in events
    assert "EXIT_FAILED" not in events


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


def test_terminal_exit_waits_for_delayed_venue_position_flatness(tmp_path, journal):
    """A fill can precede the position endpoint; one stale read is not a fault."""

    class DelayedPositionBroker(ExitBroker):
        def __init__(self):
            super().__init__()
            self.position_reads = 0

        def positions(self):
            self.position_reads += 1
            if self.position_reads == 1:
                return [SimpleNamespace(symbol=occ(), qty=Decimal(10))]
            return super().positions()

    broker = DelayedPositionBroker()
    manager, book = _ledger_manager(tmp_path, journal, broker)
    manager.mutations.poll_seconds = 0
    manager.mutations.reconcile_seconds = 0.05

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.position_reads >= 2
    assert book.entries[occ()].signed_qty == Decimal(0)
    assert occ() not in manager._exit_uncertain


def test_second_exit_attempt_uses_a_distinct_deterministic_id(tmp_path, journal):
    broker = ExitBroker(fill="6", terminal_filled="6")
    manager, _ = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))
    manager._close(ExitOrder(symbol=occ(), qty=Decimal(4), reason="target"))

    first, second = broker.submitted[0]["coid"], broker.submitted[1]["coid"]
    assert first != second
    assert broker.submitted[1]["qty"] == Decimal(4)


def test_unowned_exposure_is_refused_once_not_re_refused_every_tick(tmp_path, journal):
    """A foreign position will never become ours. Re-refusing it on a
    one-minute loop floods the journal and files a correct refusal as a
    failure."""
    broker = ExitBroker()
    manager, _ = _ledger_manager(tmp_path, journal, broker, owned=None)
    order = ExitOrder(symbol=occ(), qty=Decimal(10), reason="target")

    for _ in range(5):
        manager._close(order)

    events = [e["event"] for e in journal.read()]
    assert events.count("EXIT_REFUSED_UNOWNED") == 1, "refusal repeated every tick"
    assert "EXIT_FAILED" not in events, "a deliberate refusal was filed as a failure"
    assert broker.submitted == []
    assert broker.closed == []


def test_an_uncertain_exit_latches_instead_of_resubmitting_the_same_id(tmp_path, journal):
    """The order went out and we cannot prove where it ended. Retrying reuses
    the deterministic client id, which the venue rejects as a duplicate --
    forever, while the real fill stays unrecorded."""

    class UncertainBroker(ExitBroker):
        def cancel_and_confirm(self, order_id, client_order_id, **kw):
            from glassbox.broker import OrderStateUncertain

            raise OrderStateUncertain(f"{client_order_id} never reached a terminal state")

    broker = UncertainBroker(fill="6")
    manager, _ = _ledger_manager(tmp_path, journal, broker)
    order = ExitOrder(symbol=occ(), qty=Decimal(10), reason="target")

    for _ in range(4):
        manager._close(order)

    assert len(broker.submitted) == 1, "an unprovable exit was resubmitted"
    events = [e["event"] for e in journal.read()]
    assert events.count("EXIT_STATE_UNCERTAIN") == 1
    assert occ() in manager._exit_uncertain


def test_a_transient_close_failure_stays_retryable(tmp_path, journal):
    """The latch must not swallow ordinary failures: those should retry."""

    class FlakyBroker(ExitBroker):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def submit(self, **kw):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("connection reset")
            return super().submit(**kw)

    broker = FlakyBroker()
    manager, book = _ledger_manager(tmp_path, journal, broker)
    order = ExitOrder(symbol=occ(), qty=Decimal(10), reason="target")

    manager._close(order)
    assert "EXIT_FAILED" in [e["event"] for e in journal.read()]

    manager._close(order)
    assert broker.attempts == 2, "a transient failure was not retried"
    assert book.entries[occ()].signed_qty == Decimal(0)


def test_the_uncertainty_latch_survives_a_restart(tmp_path, journal):
    """An in-memory latch is empty after a restart, and the restart is exactly
    when the agent would resume submitting under a client id the venue has
    already rejected as a duplicate."""

    class UncertainBroker(ExitBroker):
        def cancel_and_confirm(self, order_id, client_order_id, **kw):
            from glassbox.broker import OrderStateUncertain

            raise OrderStateUncertain("never reached a terminal state")

    broker = UncertainBroker(fill="6")
    manager, book = _ledger_manager(tmp_path, journal, broker)
    order = ExitOrder(symbol=occ(), qty=Decimal(10), reason="target")
    manager._close(order)
    assert len(broker.submitted) == 1

    # A fresh manager over the same state directory: the restart.
    restarted = PositionManager(
        broker,
        journal,
        KillSwitch(tmp_path / "kill.json", journal=journal),
        targets_path=tmp_path / "targets.json",
        ledger=book,
        ledger_path=tmp_path / "ledger.json",
        exit_state_path=tmp_path / "exit_state.json",
    )
    assert occ() in restarted._exit_uncertain, "the latch did not survive the restart"

    restarted._close(order)
    assert len(broker.submitted) == 1, "a restart resumed the rejected submit loop"


def test_exit_attempt_counts_survive_a_restart(tmp_path, journal):
    """Attempt counts drive the deterministic exit id. Resetting them on
    restart would silently reuse an id whose order already exists."""
    broker = ExitBroker(fill="6", terminal_filled="6")
    manager, book = _ledger_manager(tmp_path, journal, broker)
    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))
    assert manager._exit_attempts[occ()] == 1

    restarted = PositionManager(
        broker,
        journal,
        KillSwitch(tmp_path / "kill.json", journal=journal),
        targets_path=tmp_path / "targets.json",
        ledger=book,
        ledger_path=tmp_path / "ledger.json",
        exit_state_path=tmp_path / "exit_state.json",
    )
    assert restarted._exit_attempts.get(occ()) == 1

    restarted._close(ExitOrder(symbol=occ(), qty=Decimal(4), reason="target"))
    assert broker.submitted[1]["coid"] != broker.submitted[0]["coid"]


def test_a_corrupt_exit_state_file_fails_closed(tmp_path, journal):
    from glassbox.state import StateCorrupt

    (tmp_path / "exit_state.json").write_text("{not json")
    with pytest.raises(StateCorrupt):
        PositionManager(
            ExitBroker(),
            journal,
            KillSwitch(tmp_path / "kill.json", journal=journal),
            targets_path=tmp_path / "targets.json",
            exit_state_path=tmp_path / "exit_state.json",
        )


# -- the scored account may never take the symbol-wide path --------------------


class _ScoredBroker(ExitBroker):
    env = "scored"


class _DevBroker(ExitBroker):
    env = "dev"


def test_a_scored_manager_without_a_ledger_refuses_symbol_wide_close(tmp_path, journal):
    """A comment said this must never happen; nothing enforced it. Symbol-wide
    close liquidates whatever the account holds in a contract, so reaching it
    on the scored account would undo the ledger's whole purpose."""
    broker = _ScoredBroker()
    manager = PositionManager(
        broker,
        journal,
        KillSwitch(tmp_path / "kill.json", journal=journal),
        targets_path=tmp_path / "targets.json",
        exit_state_path=tmp_path / "exit_state.json",
    )

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.closed == [], "the scored account took a symbol-wide close"
    assert broker.submitted == []
    events = [e["event"] for e in journal.read()]
    assert "EXIT_REFUSED_NO_LEDGER" in events
    assert "EXIT_FAILED" not in events, "a deliberate refusal was filed as a failure"


def test_the_development_account_still_uses_the_symbol_wide_path(tmp_path, journal):
    """The fallback is legitimate off the scored account; the guard must not
    disable the development path it was written for."""
    broker = _DevBroker()
    manager = PositionManager(
        broker,
        journal,
        KillSwitch(tmp_path / "kill.json", journal=journal),
        targets_path=tmp_path / "targets.json",
        exit_state_path=tmp_path / "exit_state.json",
    )

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.closed == [occ()]


def test_a_scored_manager_with_a_ledger_uses_the_exact_path(tmp_path, journal):
    broker = _ScoredBroker()
    manager, book = _ledger_manager(tmp_path, journal, broker)

    manager._close(ExitOrder(symbol=occ(), qty=Decimal(10), reason="target"))

    assert broker.closed == []
    assert len(broker.submitted) == 1
    assert book.entries[occ()].signed_qty == Decimal(0)


# -- banking a convexity trade that worked ------------------------------------


def _option_position(market_value: str, premium_paid: str, qty: str = "35"):
    return Position(
        symbol=occ(),
        instrument="option",
        qty=Decimal(qty),
        market_value=Decimal(market_value),
        underlying="QQQ",
        premium_paid=Decimal(premium_paid),
    )


def test_an_option_up_past_the_target_is_banked(mgr):
    """Long gamma round-trips. A strangle that doubled on Tuesday and was still
    held into Thursday, decaying, was the largest P&L leak in the system --
    options had no profit exit at all."""
    mgr.register(occ(), time_exit=datetime(2026, 9, 3, 16, 0, tzinfo=C.ET))
    position = _option_position(market_value="26000", premium_paid="17000")  # +52.9%

    exit_order = mgr._evaluate(position, state(datetime(2026, 9, 2, 11, 0, tzinfo=C.ET)))

    assert exit_order is not None
    assert "profit target hit" in exit_order.reason
    assert exit_order.qty == Decimal(35)


def test_an_option_below_the_target_is_left_alone(mgr):
    mgr.register(occ(), time_exit=datetime(2026, 9, 3, 16, 0, tzinfo=C.ET))
    position = _option_position(market_value="20000", premium_paid="17000")  # +17.6%

    assert mgr._evaluate(position, state(datetime(2026, 9, 2, 11, 0, tzinfo=C.ET))) is None


def test_a_losing_option_is_never_stopped_out(mgr):
    """Cutting a long-premium position because it is down forfeits exactly the
    optionality the premium bought, and max loss is already bounded."""
    mgr.register(occ(), time_exit=datetime(2026, 9, 3, 16, 0, tzinfo=C.ET))
    position = _option_position(market_value="4000", premium_paid="17000")  # -76%

    assert mgr._evaluate(position, state(datetime(2026, 9, 2, 11, 0, tzinfo=C.ET))) is None


def test_the_time_exit_still_wins_over_the_profit_target(mgr):
    """At measurement the position closes whether or not it is in profit."""
    deadline = datetime(2026, 9, 3, 16, 0, tzinfo=C.ET)
    mgr.register(occ(), time_exit=deadline)
    position = _option_position(market_value="18000", premium_paid="17000")

    exit_order = mgr._evaluate(position, state(deadline))
    assert exit_order is not None
    assert "time exit" in exit_order.reason


def test_an_option_with_no_recorded_premium_is_not_judged_on_profit(mgr):
    """Dividing by a cost basis we do not have would invent a return."""
    mgr.register(occ(), time_exit=datetime(2026, 9, 3, 16, 0, tzinfo=C.ET))
    position = _option_position(market_value="26000", premium_paid="0")

    assert mgr._evaluate(position, state(datetime(2026, 9, 2, 11, 0, tzinfo=C.ET))) is None


def test_equity_targets_are_unaffected(mgr):
    """The option branch must not change the path it was added in front of."""
    mgr.register("SPY", target=Decimal("700"))
    position = Position(
        symbol="SPY", instrument="equity", qty=Decimal(10), market_value=Decimal("7100")
    )
    snapshot = state(datetime(2026, 9, 2, 11, 0, tzinfo=C.ET))
    snapshot.snapshot_price["SPY"] = Decimal("710")

    exit_order = mgr._evaluate(position, snapshot)
    assert exit_order is not None
    assert "target hit" in exit_order.reason
