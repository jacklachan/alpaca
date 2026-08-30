"""The order lifecycle reducer.

These are the sequences that are painful to provoke against a live venue and
cheap to enumerate here: a duplicate poll, a stale answer arriving late, a fill
that lands after the cancel request, a replacement chain, and a status we do
not recognise.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from glassbox.order_lifecycle import (
    OrderObservation,
    all_terminal,
    any_unknown,
    apply,
    initial_state,
    normalize_status,
    reduce_observations,
    successor_qty,
)

COID = "gbx-entry-1"


def obs(status: str, filled: str = "0", *, seq: int = 0, price: str = "0", **kw):
    return OrderObservation(
        client_order_id=COID,
        status=status,
        filled_qty=Decimal(filled),
        avg_price=Decimal(price),
        sequence=seq,
        **kw,
    )


def start(qty: str = "10"):
    return initial_state(COID, Decimal(qty))


def test_normalize_status_folds_enum_reprs_and_case():
    assert normalize_status("OrderStatus.PARTIALLY_FILLED") == "partially_filled"
    assert normalize_status("  Filled ") == "filled"
    assert normalize_status(None) == ""


def test_fill_quantity_never_decreases_across_out_of_order_updates():
    """A stale read answering after a fresh one must not un-fill the order."""
    state = reduce_observations(
        start(),
        [
            obs("partially_filled", "7", seq=2, price="5.00"),
            obs("partially_filled", "3", seq=1, price="5.00"),  # stale, arrives late
        ],
    )
    assert state.filled_qty == Decimal(7)
    assert state.remaining_qty == Decimal(3)


def test_duplicate_observations_are_idempotent():
    one = apply(start(), obs("partially_filled", "4", seq=1))
    twice = apply(one, obs("partially_filled", "4", seq=1))
    assert one == twice


def test_cancel_ack_is_not_terminal_cancel():
    """pending_cancel means the venue took the request, not that it stopped."""
    state = apply(start(), obs("pending_cancel", "2", seq=1))
    assert state.cancel_requested is True
    assert state.terminal is False
    assert state.open is True


def test_late_fill_after_cancel_request_reduces_replacement_quantity():
    state = reduce_observations(
        start("10"),
        [
            obs("pending_cancel", "2", seq=1, price="5.00"),
            obs("canceled", "6", seq=2, price="5.10"),  # four more filled on the way out
        ],
    )
    assert state.terminal is True
    assert state.filled_qty == Decimal(6)
    assert successor_qty(state) == Decimal(4), "replacement would have over-ordered"


def test_terminal_state_is_durable_against_a_late_working_read():
    state = reduce_observations(
        start(),
        [
            obs("filled", "10", seq=2, price="5.00"),
            obs("new", "0", seq=1),  # stale read from before the fill
        ],
    )
    assert state.terminal is True
    assert state.status == "filled"
    assert state.filled_qty == Decimal(10)


def test_replaced_original_links_to_successor():
    state = apply(
        start(),
        obs("replaced", "3", seq=1, replaced_by_client_order_id="gbx-entry-2"),
    )
    assert state.terminal is True
    assert state.successor_client_order_id == "gbx-entry-2"
    assert successor_qty(state) == Decimal(7)


def test_unknown_status_fails_closed():
    state = apply(start(), obs("something_new_from_the_venue", "1", seq=1))
    assert state.unknown is True
    assert state.terminal is False, "an unrecognised status must never look done"
    assert any_unknown([state]) is True
    assert all_terminal([state]) is False


def test_all_terminal_requires_every_order_settled_and_known():
    filled = apply(start(), obs("filled", "10", seq=1))
    working = apply(start(), obs("partially_filled", "5", seq=1))
    assert all_terminal([filled]) is True
    assert all_terminal([filled, working]) is False


def test_engine_returns_only_when_every_entry_order_is_terminal():
    """The family of orders working one leg: terminal only when all are."""
    first = initial_state("gbx-a", Decimal(10))
    second = initial_state("gbx-b", Decimal(4))
    first = apply(first, OrderObservation("gbx-a", "replaced", Decimal(6), sequence=1))
    second = apply(second, OrderObservation("gbx-b", "pending_cancel", Decimal(1), sequence=1))
    assert all_terminal([first, second]) is False

    second = apply(second, OrderObservation("gbx-b", "canceled", Decimal(2), sequence=2))
    assert all_terminal([first, second]) is True
    total_filled = first.filled_qty + second.filled_qty
    assert total_filled == Decimal(8)


def test_observation_for_a_different_order_is_refused():
    with pytest.raises(ValueError, match="applied to"):
        apply(start(), OrderObservation("someone-elses-order", "filled", Decimal(10)))


def test_from_order_tolerates_missing_and_unparseable_fields():
    class Bare:
        status = "OrderStatus.NEW"

    observation = OrderObservation.from_order(Bare(), sequence=3)
    assert observation.status == "new"
    assert observation.filled_qty == Decimal(0)
    assert observation.sequence == 3

    class Junk:
        status = "filled"
        filled_qty = "not-a-number"
        filled_avg_price = None
        id = "broker-9"
        client_order_id = "gbx-x"

    junk = OrderObservation.from_order(Junk())
    assert junk.filled_qty == Decimal(0)
    assert junk.broker_order_id == "broker-9"


@pytest.mark.parametrize("seed", range(25))
def test_shuffled_duplicated_observations_never_overfill(seed: int):
    """Property: for any interleaving of duplicated, reordered observations
    from one true fill history, the reduced fill equals the true maximum and never
    exceeds the requested quantity."""
    rng = random.Random(seed)
    requested = Decimal(10)
    truth = [Decimal(n) for n in (0, 2, 2, 5, 9, 10)]

    stream = [
        OrderObservation(
            COID,
            "filled" if qty == requested else "partially_filled",
            qty,
            sequence=i,
        )
        for i, qty in enumerate(truth)
    ]
    # Duplicate some, then deliver the whole lot out of order.
    stream += [rng.choice(stream) for _ in range(len(stream))]
    rng.shuffle(stream)

    state = reduce_observations(initial_state(COID, requested), stream)

    assert state.filled_qty == max(truth)
    assert state.filled_qty <= requested
    assert state.remaining_qty >= Decimal(0)
