"""The trade-update stream, which is a hint and never an authority.

Every test here is a way the stream could quietly become a second source of
truth. It must not: it may advance what we believe, REST overrules it, and a
gap in it blocks new risk rather than passing unnoticed.
"""

from __future__ import annotations

import random
from decimal import Decimal
from types import SimpleNamespace

from glassbox import trade_stream as ts
from glassbox.trade_stream import TradeUpdateConsumer

COID = "gbx-entry-1"


def order(status: str, filled: str = "0", coid: str = COID, price: str = "5.00"):
    return SimpleNamespace(
        client_order_id=coid,
        status=status,
        filled_qty=filled,
        filled_avg_price=price,
        id="broker-1",
    )


def update(status: str, filled: str = "0", coid: str = COID):
    return SimpleNamespace(event=status, order=order(status, filled, coid))


def consumer(qty: str = "10") -> TradeUpdateConsumer:
    c = TradeUpdateConsumer()
    c.track(COID, Decimal(qty))
    c.on_connect()
    c.rest_reconciled()
    return c


# -- the stream may only advance belief ----------------------------------------


def test_duplicate_stream_event_is_idempotent():
    c = consumer()
    first = c.on_trade_update(update("partially_filled", "4"))
    second = c.on_trade_update(update("partially_filled", "4"))
    assert first.filled_qty == second.filled_qty == Decimal(4)
    assert c.orders[COID].filled_qty == Decimal(4)


def test_out_of_order_stream_event_cannot_reduce_fill():
    """A late event carrying an older fill must not un-fill the order."""
    c = consumer()
    c.on_trade_update(update("partially_filled", "7"))
    c.on_trade_update(update("partially_filled", "3"))
    assert c.orders[COID].filled_qty == Decimal(7)


def test_an_untracked_order_is_not_invented_and_forces_reconciliation():
    """An order we cannot attribute is REST's problem, not something to guess
    a requested quantity for."""
    c = consumer()
    assert c.on_trade_update(update("filled", "5", coid="someone-elses")) is None
    assert c.blocks_new_entries is True


def test_a_terminal_stream_event_is_recorded():
    c = consumer()
    state = c.on_trade_update(update("filled", "10"))
    assert state.terminal is True
    assert c.open_orders() == []


# -- REST wins -----------------------------------------------------------------


def test_stream_is_hint_and_rest_snapshot_wins():
    """Stream said partially filled at 4; REST says filled at 10. The snapshot
    is authoritative and must not be merged into a compromise."""
    c = consumer()
    c.on_trade_update(update("partially_filled", "4"))

    healed = c.adopt_rest_snapshot(COID, order("filled", "10"))

    assert healed.filled_qty == Decimal(10)
    assert healed.terminal is True


def test_a_late_stream_event_cannot_undo_a_rest_snapshot():
    c = consumer()
    c.adopt_rest_snapshot(COID, order("filled", "10"))
    c.on_trade_update(update("new", "0"))
    assert c.orders[COID].terminal is True
    assert c.orders[COID].filled_qty == Decimal(10)


def test_adopting_a_snapshot_for_an_untracked_order_returns_nothing():
    assert consumer().adopt_rest_snapshot("unknown", order("filled", "1")) is None


# -- gaps block risk -----------------------------------------------------------


def test_disconnect_blocks_entries_until_rest_reconciliation():
    c = consumer()
    assert c.blocks_new_entries is False

    c.on_disconnect("connection reset")
    assert c.connected is False
    assert c.blocks_new_entries is True, "a gap in the stream let new risk through"

    c.on_connect()
    assert c.blocks_new_entries is True, "reconnecting is not the same as knowing"

    c.rest_reconciled()
    assert c.blocks_new_entries is False


def test_a_fresh_connection_never_starts_trusted():
    c = TradeUpdateConsumer()
    assert c.blocks_new_entries is True
    c.on_connect()
    assert c.blocks_new_entries is True


def test_shutdown_closes_stream_and_stops_trusting_it():
    c = consumer()
    closed: list[bool] = []
    c.shutdown(close=lambda: closed.append(True))
    assert closed == [True]
    assert c.connected is False
    assert c.blocks_new_entries is True


def test_shutdown_survives_a_close_that_raises():
    c = consumer()

    def boom():
        raise RuntimeError("socket already gone")

    c.shutdown(close=boom)
    assert c.connected is False


# -- reconnect behaviour -------------------------------------------------------


def test_reconnect_backoff_is_bounded_and_jittered():
    rng = random.Random(7)
    delays = [ts.reconnect_delay(i, rng=rng) for i in range(12)]

    assert all(0 < d <= ts.MAX_RECONNECT_SECONDS for d in delays)
    assert delays[5] > delays[0], "backoff does not grow"
    # Jitter: the same attempt number must not always produce one value.
    repeated = {round(ts.reconnect_delay(4, rng=random.Random(s)), 6) for s in range(8)}
    assert len(repeated) > 1, "reconnects are synchronised and will storm"


def test_backoff_ceiling_holds_for_a_long_outage():
    assert ts.reconnect_delay(50, rng=random.Random(1)) <= ts.MAX_RECONNECT_SECONDS


def test_disconnect_returns_the_delay_and_counts_attempts():
    c = consumer()
    first = c.on_disconnect()
    second = c.on_disconnect()
    assert c.reconnect_attempts == 2
    assert first > 0 and second > 0


def test_a_successful_connect_resets_the_attempt_counter():
    c = consumer()
    c.on_disconnect()
    c.on_disconnect()
    c.on_connect()
    assert c.reconnect_attempts == 0
