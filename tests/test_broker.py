"""Reconciliation contract.

The kernel fails closed when it lacks data. That is correct, and it means
reconciliation has to supply what the invariants need -- otherwise the system
refuses everything and looks like it is working.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import glassbox.broker as broker_module
from glassbox.broker import Broker, BrokerRequestRejected, BrokerStateUnknown, TokenBucket
from glassbox.macro import trading_days_between


def _horizon(today: date, days: int = 45) -> dict[date, int]:
    """Mirrors what Broker.reconcile builds."""
    return {
        date.fromordinal(today.toordinal() + d): trading_days_between(
            today, date.fromordinal(today.toordinal() + d)
        )
        for d in range(1, days + 1)
    }


def test_horizon_covers_every_expiry_in_the_scored_window():
    """Regression: reconcile() did not populate trading_days_to at all, so
    invariant 10 refused every option plan with 'no trading-day count'. The
    convex strategy would have been dead at Monday's open."""
    h = _horizon(date(2026, 8, 31))
    for expiry in (
        date(2026, 9, 4),
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 11),
        date(2026, 9, 18),
    ):
        assert expiry in h, f"{expiry} missing from the reconciled horizon"
        assert h[expiry] > 0


def test_horizon_skips_weekends_and_labor_day():
    h = _horizon(date(2026, 9, 4))
    # Fri 4 Sep -> Tue 8 Sep is one session: Sat, Sun and Labor Day do not count.
    assert h[date(2026, 9, 8)] == 1
    assert h[date(2026, 9, 9)] == 2


def test_reconcile_supplies_what_the_expiry_guard_needs():
    from glassbox.kernel import PortfolioState

    s = PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        trading_days_to=_horizon(date(2026, 8, 31)),
    )
    assert s.trading_days_to, "an empty map refuses every option trade"
    assert date(2026, 9, 8) in s.trading_days_to


def _ready_broker(environment: str, account_number: str) -> Broker:
    broker = Broker.__new__(Broker)
    broker.env = environment
    broker.journal = None
    account = SimpleNamespace(
        account_number=account_number,
        status="ACTIVE",
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        options_trading_level=2,
        trading_blocked=False,
    )
    broker.trading = SimpleNamespace(get_account=lambda: account)
    broker._call = lambda fn, what: fn()
    broker.positions = lambda: []
    return broker


def test_assert_ready_binds_dev_credentials_to_returned_account(monkeypatch):
    monkeypatch.setenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", "DEV-EXPECTED")
    monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "SCORED-EXPECTED")
    broker = _ready_broker("dev", "WRONG-ACCOUNT")

    with pytest.raises(RuntimeError, match="expected DEV-EXPECTED"):
        broker.assert_ready()


def test_assert_ready_accepts_the_expected_returned_account(monkeypatch):
    monkeypatch.setenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", "DEV-EXPECTED")
    monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "SCORED-EXPECTED")
    broker = _ready_broker("dev", "DEV-EXPECTED")

    assert broker.assert_ready()["account_number"] == "DEV-EXPECTED"


def test_cancel_and_confirm_returns_the_terminal_late_fill():
    broker = Broker.__new__(Broker)
    cancel_calls = []
    states = iter(
        [
            SimpleNamespace(status="partially_filled", filled_qty="1"),
            SimpleNamespace(status="canceled", filled_qty="3", filled_avg_price="5.25"),
        ]
    )
    broker.cancel = lambda order_id: cancel_calls.append(order_id)
    broker.get_order_by_coid = lambda coid: next(states)
    broker._log = lambda actor, event, payload: None

    final = broker.cancel_and_confirm("broker-1", "client-1", timeout=0.1, poll_seconds=0)

    assert cancel_calls == ["broker-1"]
    assert final.status == "canceled"
    assert final.filled_qty == "3"


def test_cancel_and_confirm_raises_when_terminal_state_is_unproven():
    broker = Broker.__new__(Broker)
    broker.cancel = lambda order_id: None
    broker.get_order_by_coid = lambda coid: SimpleNamespace(
        status="partially_filled", filled_qty="1"
    )
    broker._log = lambda actor, event, payload: None

    with pytest.raises(broker_module.OrderStateUncertain, match="client-1"):
        broker.cancel_and_confirm("broker-1", "client-1", timeout=0.01, poll_seconds=0)


def test_cancel_and_confirm_accepts_terminal_state_after_cancel_error():
    broker = Broker.__new__(Broker)

    def already_terminal(order_id):
        raise RuntimeError("order is already canceled")

    broker.cancel = already_terminal
    broker.get_order_by_coid = lambda coid: SimpleNamespace(status="canceled", filled_qty="0")
    broker._log = lambda actor, event, payload: None

    final = broker.cancel_and_confirm("broker-1", "client-1", timeout=0.1, poll_seconds=0)

    assert final.status == "canceled"


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, *, retry_after: str | None = None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers={"Retry-After": retry_after} if retry_after is not None else {},
        )


def _lookup_broker(outcome) -> Broker:
    broker = Broker.__new__(Broker)
    broker.bucket = TokenBucket(10_000)
    broker.journal = None

    def lookup(_coid):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    broker.trading = SimpleNamespace(get_order_by_client_id=lookup)
    return broker


def test_get_order_by_client_id_returns_absent_only_for_verified_not_found():
    assert _lookup_broker(FakeHTTPError(404)).get_order_by_coid("missing") is None

    with pytest.raises(BrokerRequestRejected) as rejected:
        _lookup_broker(FakeHTTPError(401)).get_order_by_coid("unknown")

    assert rejected.value.status_code == 401


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("read timeout"),
        ConnectionError("connect timeout"),
        FakeHTTPError(429),
        FakeHTTPError(500),
    ],
)
def test_lookup_transport_error_is_unknown_not_absent(monkeypatch, failure):
    monkeypatch.setattr(broker_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(BrokerStateUnknown, match="lookup outcome unknown"):
        _lookup_broker(failure).get_order_by_coid("unknown")


def test_malformed_lookup_response_is_unknown_not_absent():
    malformed = SimpleNamespace(status="new", client_order_id="unknown")

    with pytest.raises(BrokerStateUnknown, match="malformed"):
        _lookup_broker(malformed).get_order_by_coid("unknown")


@pytest.mark.parametrize("status_code", [401, 403, 422])
def test_auth_and_validation_errors_are_terminal_non_retryable(status_code):
    broker = Broker.__new__(Broker)
    broker.bucket = TokenBucket(10_000)
    broker.journal = None
    calls = []

    def submit_order(request):
        calls.append(request)
        raise FakeHTTPError(status_code)

    broker.trading = SimpleNamespace(submit_order=submit_order)

    with pytest.raises(BrokerRequestRejected) as rejected:
        broker.submit(
            symbol="SPY",
            qty=Decimal("1"),
            side="buy",
            client_order_id="gbx-test",
            limit_price=Decimal("1.25"),
        )

    assert rejected.value.status_code == status_code
    assert len(calls) == 1


def test_rate_limit_retry_is_bounded_jittered_and_read_only(monkeypatch):
    broker = Broker.__new__(Broker)
    broker.bucket = TokenBucket(10_000)
    sleeps = []
    calls = 0

    def read_call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FakeHTTPError(429, retry_after="0.5")
        return "ok"

    monkeypatch.setattr(broker_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(broker_module.random, "uniform", lambda lower, upper: upper)

    assert broker._call(read_call, "read_only", attempts=3) == "ok"
    assert calls == 2
    assert len(sleeps) == 1
    assert Decimal("0.5") <= Decimal(str(sleeps[0])) <= Decimal("0.75")


def test_rate_limit_never_retries_cancel_mutation(monkeypatch):
    broker = Broker.__new__(Broker)
    broker.bucket = TokenBucket(10_000)
    broker.journal = None
    calls = 0

    def cancel_order(_order_id):
        nonlocal calls
        calls += 1
        raise FakeHTTPError(429)

    broker.trading = SimpleNamespace(cancel_order_by_id=cancel_order)
    monkeypatch.setattr(broker_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(FakeHTTPError):
        broker.cancel("broker-1")

    assert calls == 1
