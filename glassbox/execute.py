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
from .ids import client_order_id
from .schema import TradePlan, Verdict

log = logging.getLogger("glassbox.execute")

TERMINAL_OK = {"filled"}
TERMINAL_DEAD = {"canceled", "cancelled", "expired", "rejected", "suspended"}


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
    settled_qty: Decimal = Decimal(0)       # banked from superseded orders
    settled_notional: Decimal = Decimal(0)  # price * qty of those fills
    current_qty: Decimal = Decimal(0)       # fill on the order now working
    current_avg: Decimal = Decimal(0)
    client_order_id: str = ""
    broker_order_id: str = ""
    status: str = "unsubmitted"

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
        self.current_qty = 0
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

    # 100 is the OPTION contract multiplier. Applying it to equity and crypto
    # legs overstated those by 100x in the journal.
    multiplier: int = 100

    @property
    def premium_paid(self) -> Decimal:
        return sum((l.avg_price * l.filled_qty * self.multiplier
                    for l in self.legs), Decimal(0))


class ExecutionEngine:
    def __init__(self, broker, journal, *, poll_seconds: float = 3.0,
                 fill_wait_seconds: float = 45.0, max_reprice: int = 2):
        self.broker = broker
        self.journal = journal
        self.poll_seconds = poll_seconds
        self.fill_wait_seconds = fill_wait_seconds
        self.max_reprice = max_reprice

    # -- entry point -----------------------------------------------------------

    def execute(self, plan: TradePlan, verdict: Verdict) -> ExecutionResult:
        if not verdict.approved:
            raise ValueError("refusing to execute a plan the kernel did not approve")

        self.journal.append("execute", "EXECUTION_STARTED", {
            "plan_id": plan.plan_id, "sleeve": plan.sleeve,
            "instrument": plan.instrument, "symbol": plan.symbol,
            "legs": len(plan.option_legs) or 1})

        if plan.instrument == "option":
            result = self._execute_legged(plan)
        else:
            result = self._execute_single(plan)

        self.journal.append("execute", "EXECUTION_FINISHED", {
            "plan_id": plan.plan_id, "ok": result.ok, "reason": result.reason,
            "premium_paid": str(result.premium_paid), "unwound": result.unwound,
            "legs": [{"symbol": l.symbol, "requested": l.requested_qty,
                      "filled": l.filled_qty, "avg_price": str(l.avg_price),
                      "broker_order_id": l.broker_order_id} for l in result.legs]})
        return result

    # -- options ---------------------------------------------------------------

    def _execute_legged(self, plan: TradePlan) -> ExecutionResult:
        legs: list[LegResult] = []

        for i, leg in enumerate(plan.option_legs):
            coid = client_order_id(plan.plan_id, i, event=plan.is_event_trade)
            r = LegResult(leg_index=i, symbol=leg.symbol,
                          requested_qty=Decimal(leg.qty), client_order_id=coid)
            try:
                order = self.broker.submit(
                    symbol=leg.symbol, qty=leg.qty, side=leg.side,
                    client_order_id=coid, limit_price=leg.limit_price,
                    instrument="option")
                r.broker_order_id = str(order.id)
                r.status = "submitted"
            except Exception as exc:
                r.status = f"submit_failed: {exc}"
                self.journal.append("execute", "LEG_SUBMIT_FAILED", {
                    "plan_id": plan.plan_id, "leg": i,
                    "symbol": leg.symbol, "error": str(exc)})
            legs.append(r)

        self._await_fills(legs)

        if all(l.complete for l in legs):
            return ExecutionResult(plan.plan_id, True, "all legs filled", legs)

        # Re-price the laggards, bounded, never beyond the tolerance band.
        for attempt in range(1, self.max_reprice + 1):
            incomplete = [l for l in legs if not l.complete]
            if not incomplete:
                break
            self.journal.append("execute", "LEG_REPRICING", {
                "plan_id": plan.plan_id, "attempt": attempt,
                "legs": [l.symbol for l in incomplete]})
            for r in incomplete:
                self._reprice(plan, r, attempt)
            self._await_fills([l for l in legs if not l.complete])

        if all(l.complete for l in legs):
            return ExecutionResult(plan.plan_id, True,
                                   "all legs filled after repricing", legs)

        # Could not complete. A flat book beats a naked directional leg into a
        # scheduled macro print.
        filled = [l for l in legs if l.filled_qty > 0]
        if not filled:
            return ExecutionResult(plan.plan_id, False,
                                   "no legs filled; nothing to unwind", legs)

        self.journal.append("execute", "UNWIND_STARTED", {
            "plan_id": plan.plan_id,
            "reason": "could not complete all legs; refusing to hold an "
                      "unbalanced position into the event",
            "filled_legs": [{"symbol": l.symbol, "qty": l.filled_qty} for l in filled]})

        unwound = True
        for r in filled:
            try:
                self.broker.close_position(r.symbol)
            except Exception as exc:
                unwound = False
                self.journal.append("execute", "UNWIND_FAILED", {
                    "plan_id": plan.plan_id, "symbol": r.symbol, "error": str(exc)})

        return ExecutionResult(
            plan.plan_id, False,
            "legs incomplete; position unwound to flat" if unwound
            else "legs incomplete; UNWIND FAILED -- manual intervention required",
            legs, unwound=unwound)

    def _reprice(self, plan: TradePlan, r: LegResult, attempt: int) -> None:
        """Cancel the remainder and resubmit wider, within the band."""
        leg = plan.option_legs[r.leg_index]
        if r.broker_order_id:
            try:
                self.broker.cancel(r.broker_order_id)
            except Exception:
                pass  # already terminal; reconciliation will show the truth

        # Bank whatever the cancelled order filled BEFORE computing what is
        # left, so the remainder is the true shortfall and never re-orders
        # quantity the broker has already given us.
        r.bank()

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
            self.journal.append("execute", "REPRICE_REFUSED", {
                "plan_id": plan.plan_id, "symbol": r.symbol,
                "reason": f"next limit would exceed the {C.LIMIT_PRICE_BAND_PCT:.0%} band"})
            return
        new_limit = (base * (1 + bump)).quantize(Decimal("0.01"))

        # New client_order_id: this is a genuinely new order, and reusing the
        # id would be rejected as a duplicate. Offset keeps it deterministic.
        coid = client_order_id(plan.plan_id, 100 * attempt + r.leg_index,
                               event=plan.is_event_trade)
        try:
            order = self.broker.submit(
                symbol=r.symbol, qty=remaining, side=leg.side,
                client_order_id=coid, limit_price=new_limit, instrument="option")
            r.broker_order_id = str(order.id)
            r.client_order_id = coid
            r.status = "repriced"
        except Exception as exc:
            r.status = f"reprice_failed: {exc}"

    # -- equity and crypto -----------------------------------------------------

    def _execute_single(self, plan: TradePlan) -> ExecutionResult:
        coid = client_order_id(plan.plan_id, 0, event=plan.is_event_trade)
        px = None
        if plan.instrument in ("equity", "crypto"):
            prices = self.broker.snapshot_prices([plan.symbol])
            px = prices.get(plan.symbol)
        if not px or px <= 0:
            return ExecutionResult(plan.plan_id, False,
                                   f"no snapshot price for {plan.symbol}", [],
                                   multiplier=1)

        qty = (plan.notional_usd / px)
        qty = qty.quantize(Decimal("0.0001")) if plan.instrument == "crypto" \
            else Decimal(int(qty))
        if qty <= 0:
            return ExecutionResult(plan.plan_id, False, "computed quantity is zero",
                                   [], multiplier=1)

        # Marketable limit rather than a market order: paper fills are
        # unrealistically generous and a market order would hide that.
        pad = C.LIMIT_TOLERANCE if plan.side == "buy" else -C.LIMIT_TOLERANCE
        limit = (px * (1 + pad)).quantize(Decimal("0.01"))

        r = LegResult(leg_index=0, symbol=plan.symbol, requested_qty=qty,
                      client_order_id=coid)
        try:
            order = self.broker.submit(
                symbol=plan.symbol, qty=qty, side=plan.side,
                client_order_id=coid, limit_price=limit,
                instrument=plan.instrument)
            r.broker_order_id = str(order.id)
            r.status = "submitted"
        except Exception as exc:
            r.status = f"submit_failed: {exc}"
            return ExecutionResult(plan.plan_id, False, f"submit failed: {exc}", [r],
                                   multiplier=1)

        self._await_fills([r])
        ok = r.filled_qty > 0
        return ExecutionResult(plan.plan_id, ok,
                               "filled" if ok else "no fill within the wait window",
                               [r], multiplier=1)

    # -- fill polling ----------------------------------------------------------

    def _await_fills(self, legs: list[LegResult]) -> None:
        if not legs:
            return
        deadline = time.monotonic() + self.fill_wait_seconds
        while time.monotonic() < deadline:
            pending = False
            for r in legs:
                if r.complete or not r.client_order_id:
                    continue
                o = self.broker.get_order_by_coid(r.client_order_id)
                if o is None:
                    pending = True
                    continue
                # The broker reports the fill on THIS order only. Anything
                # filled by a superseded order lives in settled_qty.
                r.current_qty = Decimal(str(getattr(o, "filled_qty", 0) or 0))
                avg = getattr(o, "filled_avg_price", None)
                if avg:
                    r.current_avg = Decimal(str(avg))
                r.status = str(getattr(o, "status", "")).lower().split(".")[-1]
                if r.status in TERMINAL_DEAD:
                    continue
                if not r.complete:
                    pending = True
            if not pending:
                return
            time.sleep(self.poll_seconds)
