"""Pure Alpaca order-observation reducer.

REST polling and an optional future stream may deliver duplicates or stale
observations. This module owns the monotonic lifecycle rules; it performs no
I/O and never submits, cancels, or replaces an order.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .state import StateError

ACTIVE_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "pending_new",
    "new",
    "partially_filled",
    "pending_cancel",
    "held",
}
TERMINAL_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "replaced",
    "suspended",
    "done_for_day",
    "stopped",
    "calculated",
}
KNOWN_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


class OrderLifecycleError(StateError):
    """An order observation violates a fail-closed lifecycle invariant."""


class UnknownOrderStatus(OrderLifecycleError):
    """The venue returned a status this binary does not understand."""


@dataclass(frozen=True)
class OrderObservation:
    order_id: str
    client_order_id: str
    status: str
    cumulative_filled_qty: Decimal
    filled_avg_price: Decimal | None = None
    replaced_by_order_id: str | None = None


@dataclass(frozen=True)
class OrderLifecycle:
    order_id: str
    client_order_id: str
    requested_qty: Decimal
    status: str = "pending_new"
    cumulative_filled_qty: Decimal = Decimal("0")
    filled_avg_price: Decimal | None = None
    replaced_by_order_id: str | None = None

    @classmethod
    def start(
        cls,
        *,
        order_id: str,
        client_order_id: str,
        requested_qty: Decimal,
    ) -> "OrderLifecycle":
        if not order_id or not client_order_id or requested_qty <= 0:
            raise OrderLifecycleError("invalid order lifecycle identity or quantity")
        return cls(
            order_id=order_id,
            client_order_id=client_order_id,
            requested_qty=requested_qty,
        )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def remaining_qty(self) -> Decimal:
        return self.requested_qty - self.cumulative_filled_qty


def normalize_status(value: object) -> str:
    return str(getattr(value, "value", value)).lower().split(".")[-1]


def reduce_order(state: OrderLifecycle, observation: OrderObservation) -> OrderLifecycle:
    """Apply one observation without allowing fill or terminal-state regression."""
    status = normalize_status(observation.status)
    if status not in KNOWN_STATUSES:
        raise UnknownOrderStatus(f"unknown Alpaca order status: {status}")
    if observation.order_id != state.order_id:
        raise OrderLifecycleError("order ID changed within one lifecycle")
    if observation.client_order_id != state.client_order_id:
        raise OrderLifecycleError("client order ID changed within one lifecycle")
    if observation.cumulative_filled_qty < 0:
        raise OrderLifecycleError("cumulative fill cannot be negative")
    if observation.cumulative_filled_qty > state.requested_qty:
        raise OrderLifecycleError("cumulative fill exceeds requested quantity")

    fill_increased = observation.cumulative_filled_qty >= state.cumulative_filled_qty
    cumulative = max(state.cumulative_filled_qty, observation.cumulative_filled_qty)
    next_status = state.status if state.terminal and status not in TERMINAL_STATUSES else status
    average = (
        observation.filled_avg_price
        if fill_increased and observation.filled_avg_price is not None
        else state.filled_avg_price
    )
    successor = observation.replaced_by_order_id or state.replaced_by_order_id
    if status == "replaced" and not successor:
        raise OrderLifecycleError("replaced order is missing successor identity")

    return replace(
        state,
        status=next_status,
        cumulative_filled_qty=cumulative,
        filled_avg_price=average,
        replaced_by_order_id=successor,
    )
