"""Turns approved plans into orders. No model in this path.

The interesting problem here is legging a two-sided option position.

We submit the strangle as two independent single-leg orders rather than one
multi-leg order. That removes any dependence on multi-leg support and on the
account's approval level -- but it introduces the failure this module exists to
handle: one leg fills and the other does not.

Alpaca paper accounts partially fill roughly 10% of orders at random. An
unbalanced strangle is therefore a documented ~10% event, not a tail risk, and
if it happens on Thursday it silently converts a delta-neutral volatility
position into a directional bet on the single day that decides the score.

The completion rule, in order:
  1. Submit both legs at a marketable limit.
  2. Wait. If both fill, done.
  3. If one is short, re-price the laggard wider, up to a bounded number of
     attempts, never beyond the tolerance band.
  4. If it still will not complete, UNWIND the filled side back to flat and
     log why. A flat book is an acceptable outcome. A naked directional leg
     into a macro print is not.

Every step is journalled, including the unwind.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from . import config as C
from .broker import BrokerError
from .ids import client_order_id, unwind_client_order_id
from .mutations import MutationReceipt, MutationRequest, OrderMutationService
from .order_lifecycle import OrderObservation, OrderState
from .position_ledger import PositionLedger
from .schema import TradePlan, Verdict

log = logging.getLogger("glassbox.execute")

TERMINAL_OK = {"filled"}
TERMINAL_DEAD = {
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "suspended",
    "done_for_day",
    "replaced",
}


@dataclass
class LegResult:
    """One leg, across however many orders it took to fill it.

    A leg can be worked by more than one order: `_reprice` cancels the
    remainder and resubmits wider under a new client_order_id. Fills from the
    superseded orders are banked in `settled_qty`, because the broker only
    reports the fill on the order you ask about.

    Not banking them was a real bug. `_await_fills` overwrote `filled_qty` with
    the new order's fill, which erased the original partial. Two things then
    went wrong at once: `remaining` was recomputed from the reset value, so the
    agent ordered MORE than the kernel approved (measured at 14 contracts
    against an approved 10, a 21.9% breach of the max-loss invariant); and
    `complete` stayed False on a leg that was actually full, so a correctly
    filled strangle was unwound.
    """

    leg_index: int
    symbol: str
    # Decimal, not int. Crypto quantities are fractional -- int(0.0968) is 0,
    # which made `complete` true at zero fill and reported a filled order as a
    # failure. The same truncation hit the fill side.
    requested_qty: Decimal
    settled_qty: Decimal = Decimal(0)  # banked from superseded orders
    settled_notional: Decimal = Decimal(0)  # price * qty of those fills
    current_qty: Decimal = Decimal(0)  # fill on the order now working
    current_avg: Decimal = Decimal(0)
    client_order_id: str = ""
    broker_order_id: str = ""
    status: str = "unsubmitted"
    order_state_uncertain: bool = False
    current_order_qty: Decimal = Decimal(0)

    @property
    def filled_qty(self) -> Decimal:
        return self.settled_qty + self.current_qty

    @property
    def avg_price(self) -> Decimal:
        total = self.filled_qty
        if total <= 0:
            return Decimal(0)
        return (self.settled_notional + self.current_avg * self.current_qty) / total

    def bank(self) -> None:
        """Move the working order's fill into the settled total.

        Called before resubmitting, so the next poll adds to this leg's history
        rather than replacing it.
        """
        self.settled_notional += self.current_avg * self.current_qty
        self.settled_qty += self.current_qty
        self.current_qty = Decimal(0)
        self.current_avg = Decimal(0)

    @property
    def complete(self) -> bool:
        return self.filled_qty >= self.requested_qty

    @property
    def partial(self) -> bool:
        return 0 < self.filled_qty < self.requested_qty


@dataclass
class ExecutionResult:
    plan_id: str
    ok: bool
    reason: str
    legs: list[LegResult] = field(default_factory=list)
    unwound: bool = False
    ledger_recorded: bool = False

    # 100 is the OPTION contract multiplier. Applying it to equity and crypto
    # legs overstated those by 100x in the journal.
    multiplier: int = 100

    @property
    def premium_paid(self) -> Decimal:
        return sum((l.avg_price * l.filled_qty * self.multiplier for l in self.legs), Decimal(0))


class ExecutionEngine:
    def __init__(
        self,
        broker,
        journal,
        *,
        poll_seconds: float = 3.0,
        fill_wait_seconds: float = 45.0,
        max_reprice: int = 2,
        unknown_lookup_tolerance: int = 3,
        ledger: PositionLedger | None = None,
        ledger_path=None,
        max_unwind: int = 2,
    ):
        self.broker = broker
        self.journal = journal
        self.poll_seconds = poll_seconds
        self.fill_wait_seconds = fill_wait_seconds
        self.max_reprice = max_reprice
        self.unknown_lookup_tolerance = unknown_lookup_tolerance
        self.ledger = ledger
        self.max_unwind = max_unwind
        self.mutations = OrderMutationService(
            broker,
            journal,
            ledger=ledger,
            ledger_path=ledger_path,
            poll_seconds=poll_seconds,
            reconcile_seconds=fill_wait_seconds,
        )

    # -- entry point -----------------------------------------------------------

    def execute(self, plan: TradePlan, verdict: Verdict) -> ExecutionResult:
        if not verdict.approved:
            raise ValueError("refusing to execute a plan the kernel did not approve")

        self.journal.append(
            "execute",
            "EXECUTION_STARTED",
            {
                "plan_id": plan.plan_id,
                "sleeve": plan.sleeve,
                "instrument": plan.instrument,
                "symbol": plan.symbol,
                "legs": len(plan.option_legs) or 1,
            },
        )

        if plan.instrument == "option":
            result = self._execute_legged(plan)
        else:
            result = self._execute_single(plan)
        result.ledger_recorded = self.ledger is not None

        self.journal.append(
            "execute",
            "EXECUTION_FINISHED",
            {
                "plan_id": plan.plan_id,
                "ok": result.ok,
                "reason": result.reason,
                "premium_paid": str(result.premium_paid),
                "unwound": result.unwound,
                "legs": [
                    {
                        "symbol": l.symbol,
                        "requested": l.requested_qty,
                        "filled": l.filled_qty,
                        "avg_price": str(l.avg_price),
                        "broker_order_id": l.broker_order_id,
                    }
                    for l in result.legs
                ],
            },
        )
        return result

    def _submit_with_reconciliation(
        self,
        plan: TradePlan,
        *,
        symbol: str,
        qty: Decimal | int,
        side: str,
        client_order_id: str,
        limit_price: Decimal | None,
        instrument: str,
        purpose: str = "entry",
        plan_qty: Decimal | None = None,
    ):
        """Delegate every mutation to the single intent-first lifecycle."""
        request = MutationRequest(
            plan_id=plan.plan_id,
            symbol=symbol,
            qty=Decimal(qty),
            side=side,
            client_order_id=client_order_id,
            limit_price=limit_price,
            instrument=instrument,
            purpose=purpose,
            plan_qty=plan_qty,
        )
        return self.mutations.submit_once(
            request,
            lookup_seconds=min(self.fill_wait_seconds, 5.0),
        ).order

    # -- options ---------------------------------------------------------------

    def _execute_legged(self, plan: TradePlan) -> ExecutionResult:
        legs: list[LegResult] = []

        for i, leg in enumerate(plan.option_legs):
            coid = client_order_id(plan.plan_id, i, event=plan.is_event_trade)
            r = LegResult(
                leg_index=i,
                symbol=leg.symbol,
                requested_qty=Decimal(leg.qty),
                client_order_id=coid,
                current_order_qty=Decimal(leg.qty),
            )
            try:
                order = self._submit_with_reconciliation(
                    plan,
                    symbol=leg.symbol,
                    qty=leg.qty,
                    side=leg.side,
                    client_order_id=coid,
                    limit_price=leg.limit_price,
                    instrument="option",
                    plan_qty=Decimal(leg.qty),
                )
                r.broker_order_id = str(order.id)
                r.status = "submitted"
            except Exception as exc:
                r.status = f"submit_failed: {exc}"
                self.journal.append(
                    "execute",
                    "LEG_SUBMIT_FAILED",
                    {"plan_id": plan.plan_id, "leg": i, "symbol": leg.symbol, "error": str(exc)},
                )
            legs.append(r)

        self._await_fills(plan, legs)

        if all(l.complete for l in legs):
            return ExecutionResult(plan.plan_id, True, "all legs filled", legs)

        # Re-price the laggards, bounded, never beyond the tolerance band.
        for attempt in range(1, self.max_reprice + 1):
            incomplete = [l for l in legs if not l.complete]
            if not incomplete:
                break
            self.journal.append(
                "execute",
                "LEG_REPRICING",
                {
                    "plan_id": plan.plan_id,
                    "attempt": attempt,
                    "legs": [l.symbol for l in incomplete],
                },
            )
            for r in incomplete:
                self._reprice(plan, r, attempt)
            self._await_fills(plan, [l for l in legs if not l.complete])

        if all(l.complete for l in legs):
            return ExecutionResult(plan.plan_id, True, "all legs filled after repricing", legs)

        # Nothing may remain live after execution returns. Confirm terminal
        # state and capture any fill that landed during cancellation before
        # deciding what quantity must be unwound.
        cleanup_ok = True
        for r in (leg for leg in legs if not leg.complete):
            if not self._cancel_leg(plan, r):
                cleanup_ok = False
                continue
            r.bank()
        if not cleanup_ok:
            return ExecutionResult(
                plan.plan_id,
                False,
                "residual order state uncertain; manual intervention required",
                legs,
            )

        # Could not complete. A flat book beats a naked directional leg into a
        # scheduled macro print.
        filled = [l for l in legs if l.filled_qty > 0]
        if not filled:
            return ExecutionResult(plan.plan_id, False, "no legs filled; nothing to unwind", legs)

        self.journal.append(
            "execute",
            "UNWIND_STARTED",
            {
                "plan_id": plan.plan_id,
                "reason": "could not complete all legs; refusing to hold an "
                "unbalanced position into the event",
                "filled_legs": [{"symbol": l.symbol, "qty": l.filled_qty} for l in filled],
            },
        )

        unwound = self._unwind_exact(plan, filled)

        return ExecutionResult(
            plan.plan_id,
            False,
            "legs incomplete; position unwound to flat"
            if unwound
            else "legs incomplete; UNWIND FAILED -- manual intervention required",
            legs,
            unwound=unwound,
        )

    def _reprice(self, plan: TradePlan, r: LegResult, attempt: int) -> None:
        """Cancel the remainder and resubmit wider, within the band."""
        leg = plan.option_legs[r.leg_index]
        if r.order_state_uncertain or not self._cancel_leg(plan, r):
            return

        # Bank the terminal order's final fill before computing what is left.
        # This includes fills that arrived after the cancel request.
        r.bank()
        r.broker_order_id = ""
        r.client_order_id = ""
        r.status = "canceled"

        remaining = r.requested_qty - r.filled_qty
        if remaining <= 0:
            return

        base = leg.limit_price or Decimal(0)
        if base <= 0:
            return
        # The strategy's limit already carries LIMIT_TOLERANCE, so bumping by
        # tolerance again would compound: ask*1.03 then *1.03 is 6% over the
        # reference, past the 5% band the journal claims we never cross. Step
        # from the reference price, not from the padded limit.
        bump = C.REPRICE_STEP_PCT * Decimal(attempt + 1)
        if C.LIMIT_TOLERANCE + bump > C.LIMIT_PRICE_BAND_PCT:
            self.journal.append(
                "execute",
                "REPRICE_REFUSED",
                {
                    "plan_id": plan.plan_id,
                    "symbol": r.symbol,
                    "reason": f"next limit would exceed the {C.LIMIT_PRICE_BAND_PCT:.0%} band",
                },
            )
            return
        new_limit = (base * (1 + bump)).quantize(Decimal("0.01"))

        # New client_order_id: this is a genuinely new order, and reusing the
        # id would be rejected as a duplicate. Offset keeps it deterministic.
        coid = client_order_id(plan.plan_id, 100 * attempt + r.leg_index, event=plan.is_event_trade)
        r.client_order_id = coid
        r.current_order_qty = remaining
        try:
            order = self._submit_with_reconciliation(
                plan,
                symbol=r.symbol,
                qty=remaining,
                side=leg.side,
                client_order_id=coid,
                limit_price=new_limit,
                instrument="option",
                plan_qty=r.requested_qty,
            )
            r.broker_order_id = str(order.id)
            r.status = "repriced"
        except Exception as exc:
            r.status = f"reprice_failed: {exc}"

    def _cancel_leg(self, plan: TradePlan, r: LegResult) -> bool:
        """Cancel one working order and refresh its terminal fill ledger."""
        if r.order_state_uncertain:
            return False
        if not r.broker_order_id or r.status in TERMINAL_OK | TERMINAL_DEAD:
            return True
        try:
            request = self._entry_request(plan, r)
            state = OrderState(
                client_order_id=r.client_order_id,
                requested_qty=r.current_order_qty,
                broker_order_id=r.broker_order_id,
                status=r.status,
                filled_qty=r.current_qty,
                avg_price=r.current_avg,
            )
            final = self.mutations.cancel_and_confirm(
                MutationReceipt(request=request, order=None, state=state)
            ).order
        except Exception as exc:
            r.order_state_uncertain = True
            r.status = "cancel_uncertain"
            self.journal.append(
                "execute",
                "RESIDUAL_ORDER_UNCERTAIN",
                {
                    "plan_id": plan.plan_id,
                    "symbol": r.symbol,
                    "client_order_id": r.client_order_id,
                    "broker_order_id": r.broker_order_id,
                    "error": str(exc),
                },
            )
            return False
        self._refresh_leg(r, final)
        return r.status in TERMINAL_OK | TERMINAL_DEAD

    @staticmethod
    def _refresh_leg(r: LegResult, order) -> None:
        """Fold the broker's view of the working order into the leg.

        Monotonic on purpose. Alpaca can answer an older poll after a newer
        one, and `remaining` is derived from this number -- letting it regress
        is how the engine orders a quantity it has already filled. See
        glassbox/order_lifecycle.py for the same rule stated as a reducer.
        """
        observation = OrderObservation.from_order(order)
        if observation.filled_qty >= r.current_qty:
            r.current_qty = observation.filled_qty
            r.current_avg = observation.avg_price
        r.status = observation.status or r.status

    def _entry_request(self, plan: TradePlan, r: LegResult) -> MutationRequest:
        if plan.instrument == "option":
            leg = plan.option_legs[r.leg_index]
            side = leg.side
            limit_price = leg.limit_price
        else:
            side = plan.side
            limit_price = None
        return MutationRequest(
            plan_id=plan.plan_id,
            symbol=r.symbol,
            qty=r.current_order_qty,
            side=side,
            client_order_id=r.client_order_id,
            instrument=plan.instrument,
            purpose="entry",
            limit_price=limit_price,
            plan_qty=r.requested_qty,
        )

    def _unwind_exact(self, plan: TradePlan, filled: list[LegResult]) -> bool:
        """Flatten only confirmed strategy quantity; never close by symbol."""
        if self.ledger is not None:
            all_flat = True
            for r in filled:
                try:
                    outcome = self.mutations.exact_exit(
                        plan_id=plan.plan_id,
                        symbol=r.symbol,
                        purpose="unwind",
                        reason="incomplete multi-leg entry",
                        max_attempts=self.max_unwind,
                    )
                except Exception as exc:
                    all_flat = False
                    self.journal.append(
                        "execute",
                        "UNWIND_FAILED",
                        {"plan_id": plan.plan_id, "symbol": r.symbol, "error": str(exc)},
                    )
                else:
                    all_flat = all_flat and outcome.flat
            return all_flat

        # Development-only fallback: still use deterministic exact-size orders.
        all_flat = True
        for r in filled:
            remaining = r.filled_qty
            for attempt in range(self.max_unwind):
                if remaining <= 0:
                    break
                request = MutationRequest(
                    plan_id=plan.plan_id,
                    symbol=r.symbol,
                    qty=remaining,
                    side="sell",
                    client_order_id=unwind_client_order_id(plan.plan_id, r.symbol, attempt),
                    instrument="option",
                    purpose="unwind",
                    reason="incomplete multi-leg entry",
                )
                try:
                    receipt = self.mutations.submit_once(
                        request, lookup_seconds=min(self.fill_wait_seconds, 5.0)
                    )
                    receipt = self.mutations.cancel_and_confirm(receipt)
                except Exception as exc:
                    self.journal.append(
                        "execute",
                        "UNWIND_FAILED",
                        {"plan_id": plan.plan_id, "symbol": r.symbol, "error": str(exc)},
                    )
                    break
                remaining -= receipt.state.filled_qty
            all_flat = all_flat and remaining == 0
        return all_flat

    # -- equity and crypto -----------------------------------------------------

    def _execute_single(self, plan: TradePlan) -> ExecutionResult:
        coid = client_order_id(plan.plan_id, 0, event=plan.is_event_trade)
        px = None
        if plan.instrument in ("equity", "crypto"):
            prices = self.broker.snapshot_prices([plan.symbol])
            px = prices.get(plan.symbol)
        if not px or px <= 0:
            return ExecutionResult(
                plan.plan_id, False, f"no snapshot price for {plan.symbol}", [], multiplier=1
            )

        qty = plan.notional_usd / px
        qty = qty.quantize(Decimal("0.0001")) if plan.instrument == "crypto" else Decimal(int(qty))
        if qty <= 0:
            return ExecutionResult(
                plan.plan_id, False, "computed quantity is zero", [], multiplier=1
            )

        # Marketable limit rather than a market order: paper fills are
        # unrealistically generous and a market order would hide that.
        pad = C.LIMIT_TOLERANCE if plan.side == "buy" else -C.LIMIT_TOLERANCE
        limit = (px * (1 + pad)).quantize(Decimal("0.01"))

        r = LegResult(leg_index=0, symbol=plan.symbol, requested_qty=qty, client_order_id=coid)
        r.current_order_qty = qty
        try:
            order = self._submit_with_reconciliation(
                plan,
                symbol=plan.symbol,
                qty=qty,
                side=plan.side,
                client_order_id=coid,
                limit_price=limit,
                instrument=plan.instrument,
            )
            r.broker_order_id = str(order.id)
            r.status = "submitted"
        except Exception as exc:
            r.status = f"submit_failed: {exc}"
            return ExecutionResult(plan.plan_id, False, f"submit failed: {exc}", [r], multiplier=1)

        self._await_fills(plan, [r])
        if not r.complete:
            if not self._cancel_leg(plan, r):
                return ExecutionResult(
                    plan.plan_id,
                    False,
                    "residual order state uncertain; manual intervention required",
                    [r],
                    multiplier=1,
                )
            r.bank()
        ok = r.filled_qty > 0
        reason = (
            "filled"
            if r.complete
            else ("partial fill; residual canceled" if ok else "no fill; residual canceled")
        )
        return ExecutionResult(plan.plan_id, ok, reason, [r], multiplier=1)

    # -- fill polling ----------------------------------------------------------

    def _await_fills(self, plan: TradePlan, legs: list[LegResult]) -> None:
        if not legs:
            return
        deadline = time.monotonic() + self.fill_wait_seconds
        unknown: dict[str, int] = {}
        while time.monotonic() < deadline:
            pending = False
            for r in legs:
                if r.complete or r.order_state_uncertain or not r.client_order_id:
                    continue
                try:
                    o = self.broker.get_order_by_coid(r.client_order_id)
                except BrokerError as exc:
                    # We could not ask. That is not "no fill yet": the order may
                    # be working and filling right now. Tolerate a few transient
                    # failures, then fault the leg and stop touching it.
                    seen = unknown[r.client_order_id] = unknown.get(r.client_order_id, 0) + 1
                    if seen >= self.unknown_lookup_tolerance:
                        r.order_state_uncertain = True
                        r.status = "state_unknown"
                        self.journal.append(
                            "execute",
                            "ORDER_STATE_UNKNOWN",
                            {
                                "symbol": r.symbol,
                                "client_order_id": r.client_order_id,
                                "broker_order_id": r.broker_order_id,
                                "consecutive_failures": seen,
                                "error": str(exc),
                            },
                        )
                    else:
                        pending = True
                    continue
                unknown.pop(r.client_order_id, None)
                if o is None:
                    pending = True
                    continue
                # The broker reports the fill on THIS order only. Anything
                # filled by a superseded order lives in settled_qty.
                if plan.instrument == "option":
                    self.mutations.record_order(self._entry_request(plan, r), o)
                self._refresh_leg(r, o)
                if r.status in TERMINAL_DEAD:
                    continue
                if not r.complete:
                    pending = True
            if not pending:
                return
            time.sleep(self.poll_seconds)
