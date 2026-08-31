"""One intent-first lifecycle for every scored broker mutation.

The broker may accept an order and lose the response, fills may arrive while a
cancel is in flight, and a process may stop between either observation.  This
service keeps those facts in one place: deterministic identity, durable intent,
single submit, lookup adoption, terminal cancellation, idempotent fill
application, and exact per-contract reconciliation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .broker import OrderStateUncertain
from .ids import exit_client_order_id, unwind_client_order_id
from .order_lifecycle import OrderObservation, OrderState, apply, initial_state
from .position_ledger import PositionLedger, Reconciliation


@dataclass(frozen=True)
class MutationRequest:
    plan_id: str
    symbol: str
    qty: Decimal
    side: str
    client_order_id: str
    instrument: str
    purpose: str
    limit_price: Decimal | None = None
    plan_qty: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.purpose not in {"entry", "exit", "unwind"}:
            raise ValueError(f"unsupported mutation purpose {self.purpose!r}")
        if self.qty <= 0:
            raise ValueError("mutation quantity must be positive")
        if self.side not in {"buy", "sell"}:
            raise ValueError("mutation side must be buy or sell")


@dataclass(frozen=True)
class MutationReceipt:
    request: MutationRequest
    order: Any
    state: OrderState


@dataclass(frozen=True)
class ExactExitResult:
    flat: bool
    states: tuple[OrderState, ...] = ()
    reconciliation: Reconciliation | None = None


class OrderMutationService:
    def __init__(
        self,
        broker,
        journal,
        *,
        ledger: PositionLedger | None = None,
        ledger_path: str | Path | None = None,
        poll_seconds: float = 0.5,
        reconcile_seconds: float = 5.0,
    ):
        self.broker = broker
        self.journal = journal
        self.ledger = ledger
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.poll_seconds = poll_seconds
        self.reconcile_seconds = reconcile_seconds

    def _save_ledger(self) -> None:
        if self.ledger is not None and self.ledger_path is not None:
            self.ledger.save(self.ledger_path)

    def _register_intent(self, request: MutationRequest) -> None:
        if self.ledger is not None and request.purpose in {"exit", "unwind"}:
            self.ledger.register_exit_intent(request.symbol, request.client_order_id)
            self._save_ledger()
        self.journal.append(
            "mutation",
            "ORDER_SUBMIT_INTENT",
            {
                "plan_id": request.plan_id,
                "client_order_id": request.client_order_id,
                "symbol": request.symbol,
                "qty": str(request.qty),
                "side": request.side,
                "limit_price": (
                    str(request.limit_price) if request.limit_price is not None else None
                ),
                "instrument": request.instrument,
                "purpose": request.purpose,
                "reason": request.reason,
            },
        )

    def record_order(self, request: MutationRequest, order: Any) -> Decimal:
        """Apply only the positive cumulative-fill delta from this observation."""
        if self.ledger is None:
            return Decimal(0)
        observation = OrderObservation.from_order(order)
        if request.purpose == "entry":
            delta = self.ledger.record_entry_fill(
                plan_id=request.plan_id,
                symbol=request.symbol,
                client_order_id=request.client_order_id,
                filled_qty=observation.filled_qty,
                order_qty=request.qty,
                plan_qty=request.plan_qty or request.qty,
                side=request.side,
            )
        else:
            delta = self.ledger.record_exit_fill(
                symbol=request.symbol,
                client_order_id=request.client_order_id,
                filled_qty=observation.filled_qty,
                order_qty=request.qty,
                side=request.side,
            )
        if delta > 0:
            self._save_ledger()
        return delta

    def submit_once(
        self, request: MutationRequest, *, lookup_seconds: float = 5.0
    ) -> MutationReceipt:
        """Persist intent, submit once, and adopt only the original client ID."""
        self._register_intent(request)
        try:
            order = self.broker.submit(
                symbol=request.symbol,
                qty=request.qty,
                side=request.side,
                client_order_id=request.client_order_id,
                limit_price=request.limit_price,
                instrument=request.instrument,
            )
        except Exception as submit_error:
            deadline = time.monotonic() + lookup_seconds
            lookup_error: Exception | None = None
            while True:
                try:
                    order = self.broker.get_order_by_coid(request.client_order_id)
                except Exception as exc:
                    lookup_error = exc
                    order = None
                    lookup_verified_absent = False
                else:
                    lookup_verified_absent = order is None
                if order is not None:
                    self.journal.append(
                        "mutation",
                        "ORDER_SUBMIT_RECONCILED",
                        {
                            "plan_id": request.plan_id,
                            "client_order_id": request.client_order_id,
                            "broker_order_id": str(getattr(order, "id", "")),
                            "purpose": request.purpose,
                            "submit_error": str(submit_error),
                        },
                    )
                    break
                if lookup_verified_absent:
                    self.journal.append(
                        "mutation",
                        "ORDER_SUBMIT_NOT_ACCEPTED",
                        {
                            "plan_id": request.plan_id,
                            "client_order_id": request.client_order_id,
                            "purpose": request.purpose,
                            "submit_error": str(submit_error),
                        },
                    )
                    raise submit_error
                if time.monotonic() >= deadline:
                    self.journal.append(
                        "mutation",
                        "ORDER_SUBMIT_AMBIGUOUS",
                        {
                            "plan_id": request.plan_id,
                            "client_order_id": request.client_order_id,
                            "purpose": request.purpose,
                            "submit_error": str(submit_error),
                            "lookup_error": str(lookup_error) if lookup_error else None,
                        },
                    )
                    raise OrderStateUncertain(
                        f"submit outcome ambiguous for {request.client_order_id}"
                    ) from submit_error
                time.sleep(self.poll_seconds)

        state = apply(
            initial_state(
                request.client_order_id,
                request.qty,
                broker_order_id=str(getattr(order, "id", "")),
            ),
            OrderObservation.from_order(order, sequence=0),
        )
        self.record_order(request, order)
        return MutationReceipt(request=request, order=order, state=state)

    def cancel_and_confirm(self, receipt: MutationReceipt) -> MutationReceipt:
        if receipt.state.terminal and not receipt.state.unknown:
            return receipt
        final = self.broker.cancel_and_confirm(
            receipt.state.broker_order_id,
            receipt.request.client_order_id,
            timeout=self.reconcile_seconds,
            poll_seconds=self.poll_seconds,
        )
        state = apply(
            receipt.state,
            OrderObservation.from_order(final, sequence=receipt.state.last_sequence + 1),
        )
        if not state.terminal or state.unknown:
            raise OrderStateUncertain(
                f"order {receipt.request.client_order_id} was not terminal after cancellation"
            )
        self.record_order(receipt.request, final)
        return MutationReceipt(request=receipt.request, order=final, state=state)

    def exact_exit(
        self,
        *,
        plan_id: str,
        symbol: str,
        purpose: str,
        reason: str,
        first_attempt: int = 0,
        max_attempts: int = 1,
    ) -> ExactExitResult:
        """Work only the ledger-owned remainder and prove exact venue flatness."""
        if self.ledger is None:
            raise ValueError("exact exit requires a position ledger")
        states: list[OrderState] = []
        for offset in range(max_attempts):
            entry = self.ledger.entries.get(symbol)
            if entry is None:
                raise KeyError(f"no strategy-owned contract {symbol}")
            if entry.signed_qty == 0:
                break
            side = "sell" if entry.signed_qty > 0 else "buy"
            qty = abs(entry.signed_qty)
            attempt = first_attempt + offset
            coid = (
                unwind_client_order_id(plan_id, symbol, attempt)
                if purpose == "unwind"
                else exit_client_order_id(plan_id, symbol, attempt)
            )
            receipt = self.submit_once(
                MutationRequest(
                    plan_id=plan_id,
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    client_order_id=coid,
                    instrument="option",
                    purpose=purpose,
                    reason=reason,
                ),
                lookup_seconds=self.reconcile_seconds,
            )
            receipt = self.cancel_and_confirm(receipt)
            states.append(receipt.state)

        deadline = time.monotonic() + self.reconcile_seconds
        while True:
            reconciliation, venue_qty, terminal = self._reconcile_symbol(symbol)
            flat = reconciliation.ok and self.ledger.is_flat(
                symbol,
                venue_qty=venue_qty,
                exit_orders_terminal=terminal,
            )
            if flat:
                return ExactExitResult(
                    flat=True,
                    states=tuple(states),
                    reconciliation=reconciliation,
                )
            # A partial terminal fill has a known owned remainder. Waiting for
            # the position endpoint cannot make it flat; a successor order is
            # required instead.
            if self.ledger.entries[symbol].signed_qty != 0 or time.monotonic() >= deadline:
                return ExactExitResult(
                    flat=False,
                    states=tuple(states),
                    reconciliation=reconciliation,
                )
            time.sleep(self.poll_seconds)

    def _reconcile_symbol(self, symbol: str) -> tuple[Reconciliation, Decimal, bool]:
        positions = self.broker.positions()
        venue = {
            str(getattr(position, "symbol", "")): Decimal(str(getattr(position, "qty", 0) or 0))
            for position in positions
            if getattr(position, "symbol", None)
        }
        open_orders = list(self.broker.open_orders())
        reconciliation = self.ledger.reconcile(  # type: ignore[union-attr]
            venue_positions=venue,
            open_orders=open_orders,
        )
        owner = self.ledger.entries[symbol]  # type: ignore[union-attr]
        owned_open = any(
            owner.owns_coid(str(getattr(order, "client_order_id", "") or ""))
            for order in open_orders
        )
        return reconciliation, venue.get(symbol, Decimal(0)), not owned_open
