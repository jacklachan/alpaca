"""A pure reducer over observed order states.

Nothing here calls the network, reads a clock, or touches disk. It takes the
state we believe an order is in, plus one observation read from the venue, and
returns the new believed state. That purity is the point: every ugly ordering
case -- a duplicate poll, a stale response overtaking a fresh one, a fill that
lands after we asked to cancel -- becomes a table-driven test instead of a
timing experiment against a live account.

Three rules earn their place here.

  1. Cumulative fill never decreases. Alpaca can answer an older read after a
     newer one. Taking the latest number would let a leg that filled 7 report 3,
     and the caller would then order the difference a second time.

  2. A cancel acknowledgement is not a cancellation. `pending_cancel` means the
     venue accepted the request, not that the order stopped working. Treating
     it as terminal is how a "cancelled" order fills afterwards and leaves
     exposure nobody is tracking.

  3. An unrecognised status is not a safe status. It sets `unknown`, which is
     never terminal, so callers fail closed rather than assume the order is
     done.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

# Reached one of these and the order can no longer consume buying power.
TERMINAL_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "replaced",
        "done_for_day",
        "suspended",
    }
)

# Still working, or still capable of working.
LIVE_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "accepted_for_bidding",
        "partially_filled",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "calculated",
        "stopped",
        "held",
    }
)

# The venue accepted a request that has not yet taken effect.
_CANCEL_REQUESTED_STATUSES = frozenset({"pending_cancel"})
_REPLACE_REQUESTED_STATUSES = frozenset({"pending_replace"})


def normalize_status(raw: object) -> str:
    """Fold Alpaca's enum reprs and strings onto one lowercase token."""
    return str(getattr(raw, "value", raw) or "").lower().strip().split(".")[-1]


@dataclass(frozen=True)
class OrderObservation:
    """One point-in-time read of a single order.

    `sequence` orders observations as we issued them, so the reducer can tell a
    late answer from a new fact without trusting a wall clock.
    """

    client_order_id: str
    status: str
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)
    broker_order_id: str = ""
    replaced_by_client_order_id: str | None = None
    sequence: int = 0

    @classmethod
    def from_order(cls, order: object, *, sequence: int = 0) -> OrderObservation:
        """Build an observation from an Alpaca order object, defensively.

        Every field is optional at the boundary; a missing one must not raise
        here, because the caller's alternative is to guess.
        """

        def _decimal(name: str) -> Decimal:
            value = getattr(order, name, None)
            if value in (None, ""):
                return Decimal(0)
            try:
                return Decimal(str(value))
            except Exception:
                return Decimal(0)

        return cls(
            client_order_id=str(getattr(order, "client_order_id", "") or ""),
            status=normalize_status(getattr(order, "status", "")),
            filled_qty=_decimal("filled_qty"),
            avg_price=_decimal("filled_avg_price"),
            broker_order_id=str(getattr(order, "id", "") or ""),
            replaced_by_client_order_id=(
                str(getattr(order, "replaced_by", "")) or None
                if getattr(order, "replaced_by", None)
                else None
            ),
            sequence=sequence,
        )


@dataclass(frozen=True)
class OrderState:
    """What we believe about one order, derived only from observations."""

    client_order_id: str
    requested_qty: Decimal
    broker_order_id: str = ""
    status: str = "unsubmitted"
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)
    terminal: bool = False
    unknown: bool = False
    cancel_requested: bool = False
    replace_requested: bool = False
    successor_client_order_id: str | None = None
    last_sequence: int = -1

    @property
    def remaining_qty(self) -> Decimal:
        """What a successor order may still work. Never negative."""
        return max(Decimal(0), self.requested_qty - self.filled_qty)

    @property
    def complete(self) -> bool:
        return self.filled_qty >= self.requested_qty

    @property
    def open(self) -> bool:
        """True while this order could still consume buying power."""
        return not self.terminal


def initial_state(
    client_order_id: str, requested_qty: Decimal, *, broker_order_id: str = ""
) -> OrderState:
    return OrderState(
        client_order_id=client_order_id,
        requested_qty=Decimal(requested_qty),
        broker_order_id=broker_order_id,
    )


def apply(state: OrderState, observation: OrderObservation) -> OrderState:
    """Fold one observation into the believed state. Pure."""
    if observation.client_order_id and observation.client_order_id != state.client_order_id:
        raise ValueError(
            f"observation for {observation.client_order_id!r} applied to {state.client_order_id!r}"
        )

    status = normalize_status(observation.status)

    # Monotonic: a stale read can add information but never remove it.
    filled = max(state.filled_qty, observation.filled_qty)
    avg = observation.avg_price if observation.filled_qty >= state.filled_qty else state.avg_price
    if filled == 0:
        avg = Decimal(0)

    stale = observation.sequence < state.last_sequence
    # A terminal verdict is durable. A late non-terminal read must not reopen it.
    if state.terminal and status not in TERMINAL_STATUSES:
        return replace(
            state,
            filled_qty=filled,
            avg_price=avg if filled else state.avg_price,
            last_sequence=max(state.last_sequence, observation.sequence),
        )

    known = status in TERMINAL_STATUSES or status in LIVE_STATUSES
    if stale and known:
        # Keep the fill, discard the older status verdict.
        return replace(
            state,
            filled_qty=filled,
            avg_price=avg,
            last_sequence=state.last_sequence,
        )

    return replace(
        state,
        broker_order_id=observation.broker_order_id or state.broker_order_id,
        status=status or state.status,
        filled_qty=filled,
        avg_price=avg,
        terminal=status in TERMINAL_STATUSES,
        unknown=not known,
        cancel_requested=state.cancel_requested or status in _CANCEL_REQUESTED_STATUSES,
        replace_requested=state.replace_requested or status in _REPLACE_REQUESTED_STATUSES,
        successor_client_order_id=(
            observation.replaced_by_client_order_id or state.successor_client_order_id
        ),
        last_sequence=max(state.last_sequence, observation.sequence),
    )


def reduce_observations(state: OrderState, observations: list[OrderObservation]) -> OrderState:
    """Fold many observations in the order given."""
    for observation in observations:
        state = apply(state, observation)
    return state


def successor_qty(state: OrderState) -> Decimal:
    """Quantity a replacement order may request.

    This is the number a late fill has to be able to reduce. Sizing a
    replacement from the original request instead of from this value is how a
    partially filled leg ends up over-filled.
    """
    return state.remaining_qty


def all_terminal(states: list[OrderState]) -> bool:
    """True only when no order in the family can still work."""
    return all(s.terminal and not s.unknown for s in states)


def any_unknown(states: list[OrderState]) -> bool:
    return any(s.unknown for s in states)
