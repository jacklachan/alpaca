"""Trade updates as a hint. REST stays the authority.

Polling already establishes order state correctly, and this module does not
replace it. What a stream buys is latency: a fill can be folded in seconds
after it happens rather than at the next poll. What it must never buy is a
second, competing source of truth.

So the rules here are deliberately unequal:

  * A stream event may only *advance* what we believe. It is folded through
    the same reducer as a REST observation, so a duplicate is idempotent and an
    out-of-order event cannot reduce a fill.
  * A REST snapshot always wins. When the two disagree, the snapshot replaces
    the stream's view rather than merging with it.
  * A disconnect is not silence, it is missing information. While the stream
    is down -- or after any gap -- new entries are blocked until a REST
    reconciliation has run. A gap we did not notice is how a position gets
    opened on top of one we think is already closed.

The consumer is off unless explicitly enabled. Nothing in the safe polling
path depends on it, and removing the wiring cannot change order or state APIs.

**Why it is not wired into the entry gate.** Blocking new entries on a stream
gap was tried and removed: REST reconciliation already proves the book exactly
before any entry, so a gap adds no safety the authoritative check does not
already provide. Ordering the two the other way round deadlocks outright, since
REST is what heals the gap. The honest value here is latency -- folding a fill
in seconds rather than at the next poll -- and latency needs a live socket to
be worth anything. Until one is running against a real account, this stays a
tested component rather than a gate that cannot fire.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .order_lifecycle import OrderObservation, OrderState, apply, initial_state

log = logging.getLogger("glassbox.stream")

#: Reconnect backoff ceiling. Long enough to stop a storm, short enough that a
#: recovered venue is picked up within one polling interval.
MAX_RECONNECT_SECONDS = 30.0

BASE_RECONNECT_SECONDS = 1.0


def reconnect_delay(attempt: int, *, rng: random.Random | None = None) -> float:
    """Bounded, jittered backoff.

    Jittered because every client reconnecting on the same schedule after a
    venue blip reproduces the thundering herd that caused it.
    """
    picker = rng or random
    ceiling = min(BASE_RECONNECT_SECONDS * (2.0 ** max(0, attempt)), MAX_RECONNECT_SECONDS)
    return picker.uniform(ceiling / 2.0, ceiling)


@dataclass
class TradeUpdateConsumer:
    """Folds trade updates into believed order state, as a hint only."""

    #: client_order_id -> believed state
    orders: dict[str, OrderState] = field(default_factory=dict)
    connected: bool = False
    #: True until a REST reconciliation has healed a disconnect or gap.
    needs_rest_reconciliation: bool = True
    last_event_at: datetime | None = None
    reconnect_attempts: int = 0
    _sequence: int = 0

    # -- lifecycle -------------------------------------------------------------

    def track(self, client_order_id: str, requested_qty: Any) -> OrderState:
        """Begin tracking an order we submitted."""
        state = self.orders.get(client_order_id)
        if state is None:
            state = initial_state(client_order_id, requested_qty)
            self.orders[client_order_id] = state
        return state

    def on_connect(self) -> None:
        self.connected = True
        self.reconnect_attempts = 0
        # A fresh connection has no history. Anything that happened while we
        # were away is unknown until REST says otherwise.
        self.needs_rest_reconciliation = True
        log.info("trade stream connected; entries blocked pending REST reconciliation")

    def on_disconnect(self, reason: str = "") -> float:
        """Record a disconnect and return how long to wait before retrying."""
        self.connected = False
        self.needs_rest_reconciliation = True
        self.reconnect_attempts += 1
        delay = reconnect_delay(self.reconnect_attempts)
        log.warning("trade stream disconnected (%s); retrying in %.1fs", reason, delay)
        return delay

    # -- events ----------------------------------------------------------------

    def on_trade_update(self, update: Any, *, now: datetime | None = None) -> OrderState | None:
        """Fold one trade update. Returns the new state, or None if untracked.

        The event carries no authority of its own: it becomes an observation
        and goes through the same reducer a REST read does.
        """
        order = getattr(update, "order", update)
        coid = str(getattr(order, "client_order_id", "") or "")
        if not coid:
            return None

        state = self.orders.get(coid)
        if state is None:
            # An order we did not submit, or one from a previous process. Do
            # not invent a requested quantity for it; REST reconciliation owns
            # anything we cannot attribute.
            self.needs_rest_reconciliation = True
            return None

        self._sequence += 1
        observation = OrderObservation.from_order(order, sequence=self._sequence)
        updated = apply(state, observation)
        self.orders[coid] = updated
        self.last_event_at = now or datetime.now(timezone.utc)
        return updated

    def adopt_rest_snapshot(self, client_order_id: str, order: Any) -> OrderState | None:
        """Replace the believed state from an authoritative REST read.

        The snapshot wins outright. It is applied at a sequence above every
        stream event seen so far, so a late stream message cannot undo it.
        """
        state = self.orders.get(client_order_id)
        if state is None:
            return None
        self._sequence += 1
        updated = apply(state, OrderObservation.from_order(order, sequence=self._sequence))
        self.orders[client_order_id] = updated
        return updated

    def rest_reconciled(self) -> None:
        """Mark that a REST reconciliation has run and state is trustworthy."""
        self.needs_rest_reconciliation = False

    # -- gates -----------------------------------------------------------------

    @property
    def blocks_new_entries(self) -> bool:
        """True while the stream's view cannot be trusted.

        Being disconnected is not itself dangerous -- polling still works. What
        is dangerous is acting on a picture assembled across a gap.
        """
        return self.needs_rest_reconciliation

    def open_orders(self) -> list[OrderState]:
        return [s for s in self.orders.values() if not s.terminal]

    def shutdown(self, close: Callable[[], None] | None = None) -> None:
        """Close the stream and stop trusting it."""
        self.connected = False
        self.needs_rest_reconciliation = True
        if close is not None:
            try:
                close()
            except Exception as exc:  # pragma: no cover - best effort on exit
                log.warning("error closing trade stream: %s", exc)
