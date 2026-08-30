from __future__ import annotations

import random
from decimal import Decimal

import pytest

from glassbox.order_lifecycle import (
    OrderLifecycle,
    OrderObservation,
    UnknownOrderStatus,
    reduce_order,
)


def _state(qty: str = "10") -> OrderLifecycle:
    return OrderLifecycle.start(
        order_id="order-1",
        client_order_id="client-1",
        requested_qty=Decimal(qty),
    )


def _observe(status: str, filled: str, *, replaced_by: str | None = None) -> OrderObservation:
    return OrderObservation(
        order_id="order-1",
        client_order_id="client-1",
        status=status,
        cumulative_filled_qty=Decimal(filled),
        filled_avg_price=Decimal("5.25") if Decimal(filled) else None,
        replaced_by_order_id=replaced_by,
    )


def test_fill_quantity_never_decreases_across_out_of_order_updates() -> None:
    state = reduce_order(_state(), _observe("partially_filled", "4"))
    state = reduce_order(state, _observe("new", "2"))

    assert state.cumulative_filled_qty == Decimal("4")
    assert state.remaining_qty == Decimal("6")


def test_late_fill_after_cancel_request_reduces_replacement_quantity() -> None:
    state = reduce_order(_state(), _observe("pending_cancel", "2"))
    state = reduce_order(state, _observe("canceled", "5"))

    assert state.terminal
    assert state.cumulative_filled_qty == Decimal("5")
    assert state.remaining_qty == Decimal("5")


def test_cancel_ack_is_not_terminal_cancel() -> None:
    state = reduce_order(_state(), _observe("pending_cancel", "2"))

    assert not state.terminal
    assert state.status == "pending_cancel"


def test_replaced_original_links_to_successor() -> None:
    state = reduce_order(
        _state(),
        _observe("replaced", "3", replaced_by="order-2"),
    )

    assert state.terminal
    assert state.replaced_by_order_id == "order-2"


def test_unknown_status_fails_closed() -> None:
    with pytest.raises(UnknownOrderStatus, match="mystery"):
        reduce_order(_state(), _observe("mystery", "0"))


def test_duplicate_and_out_of_order_sequences_never_overfill_or_regress() -> None:
    rng = random.Random(7)
    observations = [
        _observe("accepted", "0"),
        _observe("new", "0"),
        _observe("partially_filled", "2"),
        _observe("partially_filled", "6"),
        _observe("filled", "10"),
    ]

    for _ in range(100):
        state = _state()
        sequence = observations + rng.choices(observations, k=8)
        rng.shuffle(sequence)
        previous = Decimal("0")
        for observation in sequence:
            state = reduce_order(state, observation)
            assert previous <= state.cumulative_filled_qty <= state.requested_qty
            previous = state.cumulative_filled_qty
