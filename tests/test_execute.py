"""Execution, driven by a fake broker.

The cases that matter are the ugly ones: a leg that will not fill, a partial
fill, and the unwind. Alpaca paper accounts partially fill ~10% of orders at
random, so these are documented behaviours rather than hypotheticals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from glassbox.broker import TokenBucket
from glassbox.execute import ExecutionEngine
from glassbox.journal import Journal
from glassbox.schema import OptionLeg, TradePlan, Verdict

EXP = date(2026, 9, 8)


def occ(right: str, strike: int) -> str:
    return f"SPY{EXP:%y%m%d}{right}{strike * 1000:08d}"


class FakeOrder:
    def __init__(self, oid, coid, qty):
        self.id, self.client_order_id = oid, coid
        self.filled_qty, self.filled_avg_price = 0, None
        self.status, self.qty = "new", qty
        self.submitted_at = "2026-09-03T18:00:00Z"


class FakeBroker:
    """Fill behaviour is scripted per symbol: fraction of requested qty."""

    def __init__(self, fill: dict[str, float] | None = None, price=Decimal("5.00")):
        self.fill = fill or {}
        self.price = price
        self.orders: dict[str, FakeOrder] = {}
        self.submitted: list[dict] = []
        self.closed: list[str] = []
        self.cancelled: list[str] = []
        self.bucket = TokenBucket(10_000)
        self._n = 0

    def submit(self, *, symbol, qty, side, client_order_id, limit_price=None,
               instrument="equity"):
        self._n += 1
        o = FakeOrder(f"broker-{self._n}", client_order_id, qty)
        frac = self.fill.get(symbol, 1.0)
        o.filled_qty = int(float(qty) * frac)
        if o.filled_qty:
            o.filled_avg_price = limit_price or self.price
        o.status = "filled" if o.filled_qty >= float(qty) else (
            "partially_filled" if o.filled_qty else "new")
        self.orders[client_order_id] = o
        self.submitted.append({"symbol": symbol, "qty": qty, "side": side,
                               "limit": limit_price, "coid": client_order_id})
        return o

    def get_order_by_coid(self, coid):
        return self.orders.get(coid)

    def cancel(self, order_id):
        self.cancelled.append(order_id)

    def close_position(self, symbol):
        self.closed.append(symbol)

    def snapshot_prices(self, symbols):
        return {s: Decimal("769.28") for s in symbols}


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.jsonl")


def strangle(qty: int = 10) -> TradePlan:
    return TradePlan(
        sleeve="convex", action="open", instrument="option", symbol="SPY",
        side="buy", is_event_trade=True,
        option_legs=[
            OptionLeg(symbol=occ("C", 778), side="buy", qty=qty, limit_price=Decimal("3.50")),
            OptionLeg(symbol=occ("P", 760), side="buy", qty=qty, limit_price=Decimal("3.40")),
        ],
        notional_usd=Decimal("6900"), max_loss_usd=Decimal("6900"),
        thesis="Scheduled payrolls print one hour before measurement with implied "
               "volatility near the lows. Buying the move, not the direction.",
        evidence=["macro_cal: NFP 2026-09-04 08:30 ET"], confidence=0.6)


APPROVED = lambda p: Verdict(plan_id=p.plan_id, approved=True, reason="ok",
                             checks_passed=13, checks_total=13)


def engine(broker, journal):
    return ExecutionEngine(broker, journal, poll_seconds=0, fill_wait_seconds=0.01,
                           max_reprice=2)


# --- happy path ---------------------------------------------------------------

def test_both_legs_fill():
    b = FakeBroker()
    j = Journal.__new__(Journal)
    import tempfile, pathlib
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
    v = Verdict(plan_id=p.plan_id, approved=False, reason="naked short",
                checks_passed=1, checks_total=13, failed_invariant="02_bounded_max_loss")
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
    b = TokenBucket(rate_per_min=600)   # 10/sec
    for _ in range(600):
        b.take()
    t.sleep(0.25)
    assert b.take()
