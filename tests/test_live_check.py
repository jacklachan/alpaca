"""The real-venue proof must be bounded even when fills race cancellation."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from glassbox.broker import OrderStateUncertain
import tools.live_check as live_check

LIVE_CHECK_MAX_NOTIONAL_USD = getattr(
    live_check, "LIVE_CHECK_MAX_NOTIONAL_USD", Decimal("50.00"))


def order(status: str, filled: str, avg: str = "100000"):
    return SimpleNamespace(
        id=f"order-{status}-{filled}",
        status=status,
        filled_qty=filled,
        filled_avg_price=avg,
        client_order_id="",
    )


class FakeBroker:
    def __init__(self, *, entry=None, exit=None, positions=None, open_orders=None,
                 ready_error: Exception | None = None):
        self.entry = entry or order("filled", "0.0005")
        self.exit = exit or order("filled", "0.0005")
        self.position_qty = Decimal("0")
        self.initial_positions = list(positions or [])
        self.initial_open_orders = list(open_orders or [])
        self.ready_error = ready_error
        self.submitted = []
        self.cancel_results = []
        self.close_position_calls = []
        self.force_residual_open = False

    def assert_ready(self):
        if self.ready_error:
            raise self.ready_error
        return {"account_number": "DEV-EXPECTED", "env": "dev"}

    def positions(self):
        if self.initial_positions:
            return self.initial_positions
        if self.position_qty:
            return [SimpleNamespace(symbol="BTCUSD", qty=str(self.position_qty))]
        return []

    def open_orders(self):
        if not self.submitted:
            return self.initial_open_orders
        if self.force_residual_open:
            return [SimpleNamespace(client_order_id=self.submitted[-1]["coid"])]
        return []

    def snapshot_prices(self, symbols):
        return {"BTC/USD": Decimal("100000")}

    def submit(self, *, symbol, qty, side, client_order_id, limit_price,
               instrument):
        self.submitted.append({
            "symbol": symbol, "qty": Decimal(str(qty)), "side": side,
            "coid": client_order_id, "limit": limit_price,
            "instrument": instrument,
        })
        chosen = self.entry if side == "buy" else self.exit
        chosen.client_order_id = client_order_id
        return SimpleNamespace(id=f"broker-{len(self.submitted)}")

    def get_order_by_coid(self, coid):
        chosen = self.entry if coid == self.submitted[0]["coid"] else self.exit
        if str(chosen.status).lower() in {"filled", "canceled"}:
            fill = Decimal(str(chosen.filled_qty))
            if chosen is self.entry:
                self.position_qty = fill
            else:
                self.position_qty = Decimal(str(self.entry.filled_qty)) - fill
        return chosen

    def cancel_and_confirm(self, order_id, client_order_id, **kwargs):
        result = self.cancel_results.pop(0)
        if isinstance(result, Exception):
            raise result
        if client_order_id == self.submitted[0]["coid"]:
            self.entry = result
            self.position_qty = Decimal(str(result.filled_qty))
        else:
            self.exit = result
            self.position_qty = (Decimal(str(self.entry.filled_qty))
                                 - Decimal(str(result.filled_qty)))
        return result

    def close_position(self, symbol):
        self.close_position_calls.append(symbol)
        raise AssertionError("live check must never close a symbol-wide position")


class Journal:
    def __init__(self):
        self.events = []

    def append(self, actor, event, payload):
        self.events.append((actor, event, payload))


def run(broker, notional=Decimal("50")):
    return live_check.run_trade_check(
        broker, Journal(), notional, run_id="unit-test",
        wait_seconds=0, poll_seconds=0, sleep=lambda _: None)


def test_refuses_notional_above_hard_ceiling_before_broker_access():
    broker = FakeBroker(ready_error=AssertionError("broker must not be read"))

    result = run(broker, LIVE_CHECK_MAX_NOTIONAL_USD + Decimal("0.01"))

    assert not result.ok
    assert "ceiling" in result.reason
    assert broker.submitted == []


def test_refuses_when_account_identity_is_not_proven():
    result = run(FakeBroker(ready_error=RuntimeError("wrong account")))

    assert not result.ok
    assert "account identity" in result.reason


def test_refuses_a_dirty_position_baseline():
    broker = FakeBroker(positions=[SimpleNamespace(symbol="ETHUSD", qty="1")])

    result = run(broker)

    assert not result.ok
    assert "positions" in result.reason
    assert broker.submitted == []


def test_refuses_a_dirty_open_order_baseline():
    broker = FakeBroker(open_orders=[SimpleNamespace(client_order_id="foreign")])

    result = run(broker)

    assert not result.ok
    assert "open orders" in result.reason
    assert broker.submitted == []


def test_sells_exactly_the_test_created_quantity_without_symbol_close():
    broker = FakeBroker(entry=order("filled", "0.0005"),
                        exit=order("filled", "0.0005"))

    result = run(broker)

    assert result.ok, result.reason
    assert broker.submitted[1]["side"] == "sell"
    assert broker.submitted[1]["qty"] == Decimal("0.0005")
    assert broker.close_position_calls == []
    assert broker.positions() == []


def test_late_entry_fill_after_cancel_sets_the_exact_cleanup_quantity():
    broker = FakeBroker(entry=order("partially_filled", "0.0002"),
                        exit=order("filled", "0.0007"))
    broker.cancel_results = [order("canceled", "0.0007")]

    result = run(broker)

    assert result.ok, result.reason
    assert broker.submitted[1]["qty"] == Decimal("0.0007")


def test_unconfirmed_entry_cancel_is_a_failure_not_a_warning():
    broker = FakeBroker(entry=order("partially_filled", "0.0002"))
    broker.cancel_results = [OrderStateUncertain("entry uncertain")]

    result = run(broker)

    assert not result.ok
    assert "entry" in result.reason


def test_partial_exit_and_residual_position_fail_exact_reconciliation():
    broker = FakeBroker(entry=order("filled", "0.0005"),
                        exit=order("partially_filled", "0.0002"))
    broker.cancel_results = [order("canceled", "0.0002")]

    result = run(broker)

    assert not result.ok
    assert "exact baseline" in result.reason
    assert broker.position_qty == Decimal("0.0003")


def test_residual_test_order_fails_reconciliation():
    broker = FakeBroker()
    broker.force_residual_open = True

    result = run(broker)

    assert not result.ok
    assert "open order" in result.reason
