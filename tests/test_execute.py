"""Execution, driven by a fake broker.

The cases that matter are the ugly ones: a leg that will not fill, a partial
fill, and the unwind. Alpaca paper accounts partially fill ~10% of orders at
random, so these are documented behaviours rather than hypotheticals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from glassbox.broker import BrokerUnavailable, OrderStateUncertain, TokenBucket
from glassbox.execute import ExecutionEngine
from glassbox.journal import Journal
from glassbox.schema import OptionLeg, TradePlan, Verdict

EXP = date(2026, 9, 8)


def occ(right: str, strike: int) -> str:
    return f"SPY{EXP:%y%m%d}{right}{strike * 1000:08d}"


class FakeOrder:
    def __init__(self, oid, coid, qty, symbol):
        self.id, self.client_order_id = oid, coid
        self.filled_qty, self.filled_avg_price = 0, None
        self.status, self.qty = "new", qty
        self.symbol = symbol
        self.submitted_at = "2026-09-03T18:00:00Z"


class FakeBroker:
    """Fill behaviour is scripted per symbol: fraction of requested qty."""

    def __init__(
        self,
        fill: dict[str, float] | None = None,
        price=Decimal("5.00"),
        cancel_fill: dict[str, list[float]] | None = None,
        uncertain_cancel: set[str] | None = None,
        accepted_then_timeout: set[str] | None = None,
    ):
        self.fill = fill or {}
        self.price = price
        self.cancel_fill = cancel_fill or {}
        self.uncertain_cancel = uncertain_cancel or set()
        self.accepted_then_timeout = accepted_then_timeout or set()
        self.orders: dict[str, FakeOrder] = {}
        self.submitted: list[dict] = []
        self.closed: list[str] = []
        self.cancelled: list[str] = []
        self.bucket = TokenBucket(10_000)
        self._n = 0

    def submit(self, *, symbol, qty, side, client_order_id, limit_price=None, instrument="equity"):
        self._n += 1
        o = FakeOrder(f"broker-{self._n}", client_order_id, qty, symbol)
        frac = self.fill.get(symbol, 1.0)
        o.filled_qty = Decimal(str(qty)) * Decimal(str(frac))
        if o.filled_qty:
            o.filled_avg_price = limit_price or self.price
        o.status = (
            "filled"
            if o.filled_qty >= float(qty)
            else ("partially_filled" if o.filled_qty else "new")
        )
        self.orders[client_order_id] = o
        self.submitted.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "limit": limit_price,
                "coid": client_order_id,
            }
        )
        if symbol in self.accepted_then_timeout:
            self.accepted_then_timeout.remove(symbol)
            raise TimeoutError("response lost after broker acceptance")
        return o

    def get_order_by_coid(self, coid):
        return self.orders.get(coid)

    def cancel(self, order_id):
        self.cancelled.append(order_id)

    def cancel_and_confirm(self, order_id, client_order_id, **kwargs):
        order = self.orders[client_order_id]
        if order.symbol in self.uncertain_cancel:
            raise OrderStateUncertain(f"{client_order_id} still working")
        self.cancelled.append(order_id)
        scripted = self.cancel_fill.get(order.symbol, [])
        if scripted:
            fraction = Decimal(str(scripted.pop(0)))
            order.filled_qty = Decimal(str(order.qty)) * fraction
            if order.filled_qty:
                order.filled_avg_price = self.price
        order.status = "canceled"
        return order

    def close_position(self, symbol):
        self.closed.append(symbol)

    def snapshot_prices(self, symbols):
        return {s: Decimal("769.28") for s in symbols}


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.jsonl")


def strangle(qty: int = 10) -> TradePlan:
    return TradePlan(
        sleeve="convex",
        action="open",
        instrument="option",
        symbol="SPY",
        side="buy",
        is_event_trade=True,
        option_legs=[
            OptionLeg(symbol=occ("C", 778), side="buy", qty=qty, limit_price=Decimal("3.50")),
            OptionLeg(symbol=occ("P", 760), side="buy", qty=qty, limit_price=Decimal("3.40")),
        ],
        notional_usd=Decimal("6900"),
        max_loss_usd=Decimal("6900"),
        thesis="Scheduled payrolls print one hour before measurement with implied "
        "volatility near the lows. Buying the move, not the direction.",
        evidence=["macro_cal: NFP 2026-09-04 08:30 ET"],
        confidence=0.6,
    )


def single(instrument: str) -> TradePlan:
    symbol = "BTC/USD" if instrument == "crypto" else "SPY"
    sleeve = "crypto" if instrument == "crypto" else "core"
    return TradePlan(
        sleeve=sleeve,
        action="open",
        instrument=instrument,
        symbol=symbol,
        side="buy",
        notional_usd=Decimal("1000"),
        max_loss_usd=Decimal("100"),
        thesis="A deterministic test allocation with a bounded requested size "
        "and an independently checked execution cleanup path.",
        evidence=["test fixture with literal expected quantities"],
        confidence=0.5,
    )


APPROVED = lambda p: Verdict(
    plan_id=p.plan_id, approved=True, reason="ok", checks_passed=13, checks_total=13
)


def engine(broker, journal):
    return ExecutionEngine(broker, journal, poll_seconds=0, fill_wait_seconds=0.01, max_reprice=2)


# --- happy path ---------------------------------------------------------------


def test_both_legs_fill():
    b = FakeBroker()
    j = Journal.__new__(Journal)
    import pathlib
    import tempfile

    j.__init__(pathlib.Path(tempfile.mkdtemp()) / "j.jsonl")
    p = strangle()
    r = engine(b, j).execute(p, APPROVED(p))
    assert r.ok and not r.unwound
    assert all(l.complete for l in r.legs)
    assert r.premium_paid == Decimal("6900")


def test_legs_are_submitted_separately_not_multileg(journal):
    """Legging removes the multi-leg and approval-level dependencies."""
    b = FakeBroker()
    p = strangle()
    engine(b, journal).execute(p, APPROVED(p))
    assert len(b.submitted) == 2
    assert {s["symbol"] for s in b.submitted} == {occ("C", 778), occ("P", 760)}


def test_client_order_ids_are_deterministic_and_distinct(journal):
    b = FakeBroker()
    p = strangle()
    engine(b, journal).execute(p, APPROVED(p))
    coids = [s["coid"] for s in b.submitted]
    assert len(set(coids)) == 2
    from glassbox.ids import EVENT_PREFIX, client_order_id

    assert coids[0] == client_order_id(p.plan_id, 0, event=p.is_event_trade)

    # An event trade must be identifiable from the id alone. reconcile() sees
    # broker orders, not plans, so without this marker the per-day event
    # premium cap degrades into a per-order check.
    if p.is_event_trade:
        assert all(c.startswith(EVENT_PREFIX) for c in coids)


# --- the ugly cases -----------------------------------------------------------


def test_unfilled_second_leg_is_repriced_wider(journal):
    b = FakeBroker(fill={occ("P", 760): 0.0})
    p = strangle()
    engine(b, journal).execute(p, APPROVED(p))
    put_orders = [s for s in b.submitted if s["symbol"] == occ("P", 760)]
    assert len(put_orders) > 1, "laggard must be repriced"
    assert put_orders[1]["limit"] > put_orders[0]["limit"]


def test_repricing_never_exceeds_the_tolerance_band(journal):
    from glassbox import config as C

    b = FakeBroker(fill={occ("P", 760): 0.0})
    p = strangle()
    engine(b, journal).execute(p, APPROVED(p))
    base = Decimal("3.40")
    for s in [s for s in b.submitted if s["symbol"] == occ("P", 760)]:
        assert (s["limit"] - base) / base <= C.LIMIT_PRICE_BAND_PCT


def test_incomplete_strangle_is_unwound_to_flat(journal):
    """The important one. A naked long call into payrolls is a directional bet
    the risk model never approved."""
    b = FakeBroker(fill={occ("P", 760): 0.0})
    p = strangle()
    r = engine(b, journal).execute(p, APPROVED(p))
    assert not r.ok
    assert r.unwound
    assert occ("C", 778) in b.closed, "the filled call must be closed"
    assert "unwound" in r.reason


def test_partial_fill_is_also_unwound(journal):
    b = FakeBroker(fill={occ("P", 760): 0.3})
    p = strangle()
    r = engine(b, journal).execute(p, APPROVED(p))
    assert not r.ok and r.unwound


def test_late_fill_during_cancel_reduces_the_replacement_quantity(journal):
    put = occ("P", 760)
    b = FakeBroker(fill={put: 0.2}, cancel_fill={put: [0.7]})
    p = strangle()

    engine(b, journal).execute(p, APPROVED(p))

    put_orders = [order for order in b.submitted if order["symbol"] == put]
    assert put_orders[1]["qty"] == Decimal("3")


def test_unconfirmed_cancel_never_submits_a_replacement(journal):
    put = occ("P", 760)
    b = FakeBroker(fill={put: 0.2}, uncertain_cancel={put})
    p = strangle()

    result = engine(b, journal).execute(p, APPROVED(p))

    put_orders = [order for order in b.submitted if order["symbol"] == put]
    assert len(put_orders) == 1
    assert not result.ok
    assert "manual intervention" in result.reason


def test_failed_replacement_submit_does_not_bank_predecessor_twice(journal):
    put = occ("P", 760)

    class ReplacementFails(FakeBroker):
        def submit(self, **kwargs):
            if len(self.submitted) == 2:
                raise RuntimeError("replacement rejected")
            return super().submit(**kwargs)

    broker = ReplacementFails(fill={put: 0.3})
    plan = strangle()

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    put_result = next(leg for leg in result.legs if leg.symbol == put)
    assert put_result.filled_qty == Decimal("3")


@pytest.mark.parametrize("instrument", ["equity", "crypto"])
def test_single_order_partial_fill_cancels_the_residual(journal, instrument):
    plan = single(instrument)
    broker = FakeBroker(fill={plan.symbol: 0.5})

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    assert result.ok
    assert broker.cancelled == ["broker-1"]
    assert "residual canceled" in result.reason


def test_single_order_cancel_uncertainty_is_not_success(journal):
    plan = single("equity")
    broker = FakeBroker(fill={plan.symbol: 0.5}, uncertain_cancel={plan.symbol})

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    assert not result.ok
    assert "residual order state uncertain" in result.reason


def test_submit_timeout_adopts_the_existing_client_order_without_resubmit(journal):
    plan = single("equity")
    broker = FakeBroker(accepted_then_timeout={plan.symbol})

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    assert result.ok, result.reason
    assert len(broker.submitted) == 1
    events = [entry["event"] for entry in journal.read()]
    assert "ORDER_SUBMIT_INTENT" in events
    assert "ORDER_SUBMIT_RECONCILED" in events


def test_nothing_filled_needs_no_unwind(journal):
    b = FakeBroker(fill={occ("C", 778): 0.0, occ("P", 760): 0.0})
    p = strangle()
    r = engine(b, journal).execute(p, APPROVED(p))
    assert not r.ok and not r.unwound
    assert b.closed == []


def test_failed_unwind_says_so_loudly(journal):
    b = FakeBroker(fill={occ("P", 760): 0.0})
    b.close_position = lambda s: (_ for _ in ()).throw(RuntimeError("broker down"))
    p = strangle()
    r = engine(b, journal).execute(p, APPROVED(p))
    assert not r.unwound
    assert "manual intervention" in r.reason


# --- guards and the record ----------------------------------------------------


def test_refuses_to_execute_an_unapproved_plan(journal):
    b = FakeBroker()
    p = strangle()
    v = Verdict(
        plan_id=p.plan_id,
        approved=False,
        reason="naked short",
        checks_passed=1,
        checks_total=13,
        failed_invariant="02_bounded_max_loss",
    )
    with pytest.raises(ValueError):
        engine(b, journal).execute(p, v)
    assert b.submitted == []


def test_the_unwind_is_journalled_with_its_reason(journal):
    b = FakeBroker(fill={occ("P", 760): 0.0})
    p = strangle()
    engine(b, journal).execute(p, APPROVED(p))
    events = [e["event"] for e in journal.read()]
    assert "UNWIND_STARTED" in events
    entry = next(e for e in journal.read() if e["event"] == "UNWIND_STARTED")
    assert "unbalanced" in entry["payload"]["reason"]
    ok, why = journal.verify()
    assert ok, why


def test_execution_records_broker_side_identifiers(journal):
    """Broker order ids are what a third party can reconcile against."""
    b = FakeBroker()
    p = strangle()
    r = engine(b, journal).execute(p, APPROVED(p))
    assert all(l.broker_order_id for l in r.legs)
    fin = next(e for e in journal.read() if e["event"] == "EXECUTION_FINISHED")
    assert all(l["broker_order_id"] for l in fin["payload"]["legs"])


# --- rate limiter -------------------------------------------------------------


def test_token_bucket_allows_burst_then_throttles():
    b = TokenBucket(rate_per_min=60)
    assert all(b.take() for _ in range(60))
    assert not b.take(n=5, timeout=0.05)


def test_token_bucket_refills():
    import time as t

    b = TokenBucket(rate_per_min=600)  # 10/sec
    for _ in range(600):
        b.take()
    t.sleep(0.25)
    assert b.take()


# -- unknown venue state (Task C) ---------------------------------------------


class UnknownLookupBroker(FakeBroker):
    """A broker whose order lookup cannot answer.

    This is the case that must never be read as "the order does not exist".
    """

    def __init__(self, *args, fail_submit: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_submit = fail_submit
        self.lookups = 0

    def submit(self, **kwargs):
        if self.fail_submit:
            self.submitted.append({"coid": kwargs["client_order_id"]})
            raise TimeoutError("response lost after broker acceptance")
        return super().submit(**kwargs)

    def get_order_by_coid(self, coid):
        self.lookups += 1
        raise BrokerUnavailable("venue unreachable")


def test_submit_timeout_and_failed_lookup_latches_state_and_never_retries(journal):
    """Submit once. If the outcome cannot be established, fault -- do not
    submit again, and do not report success."""
    plan = single("equity")
    broker = UnknownLookupBroker(fail_submit=True)

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    assert not result.ok
    assert len(broker.submitted) == 1, "an ambiguous submit was replayed"

    entries = list(journal.read())
    events = [entry["event"] for entry in entries]
    assert "ORDER_SUBMIT_INTENT" in events
    assert "ORDER_SUBMIT_AMBIGUOUS" in events
    assert "ORDER_SUBMIT_RECONCILED" not in events

    ambiguous = next(e for e in entries if e["event"] == "ORDER_SUBMIT_AMBIGUOUS")
    assert ambiguous["payload"]["lookup_error"], "the real lookup failure was not recorded"


def test_unknown_lookup_while_polling_fills_marks_the_leg_uncertain(journal):
    """A lookup that cannot answer is not 'no fill yet'. After the tolerance is
    spent the leg is uncertain, which blocks the residual-cancel path."""
    plan = single("equity")
    broker = UnknownLookupBroker(fill={plan.symbol: 0.0})

    result = engine(broker, journal).execute(plan, APPROVED(plan))

    assert not result.ok
    assert "uncertain" in result.reason.lower()
    events = [entry["event"] for entry in journal.read()]
    assert "ORDER_STATE_UNKNOWN" in events
    assert plan.symbol not in broker.closed, "a symbol-wide close ran on unknown state"


def test_stale_order_read_cannot_reduce_a_leg_fill(journal):
    """Regression guard for the reducer rule: `remaining` is derived from the
    leg fill, so a fill that goes backwards re-orders what already filled."""
    from glassbox.execute import LegResult

    r = LegResult(leg_index=0, symbol="SPY", requested_qty=Decimal(10))
    fresh = FakeOrder("broker-1", "gbx-1", 10, "SPY")
    fresh.filled_qty, fresh.filled_avg_price, fresh.status = 7, "5.00", "partially_filled"
    ExecutionEngine._refresh_leg(r, fresh)
    assert r.filled_qty == Decimal(7)

    stale = FakeOrder("broker-1", "gbx-1", 10, "SPY")
    stale.filled_qty, stale.filled_avg_price, stale.status = 3, "5.00", "partially_filled"
    ExecutionEngine._refresh_leg(r, stale)

    assert r.filled_qty == Decimal(7), "a stale read reduced the observed fill"
