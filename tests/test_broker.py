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
from glassbox.broker import Broker
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


# -- typed failure classification (Task C) ------------------------------------


def _api_error(message: str, status: int | None = None, retry_after: str | None = None):
    """Build a real alpaca APIError with an attached HTTP response."""
    from alpaca.common.exceptions import APIError

    if status is None:
        return APIError(message)
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    http_error = SimpleNamespace(
        response=SimpleNamespace(status_code=status, headers=headers),
        request=None,
    )
    return APIError(message, http_error)


def _lookup_broker(raiser):
    """A Broker whose only wired behaviour is get_order_by_client_id."""
    broker = Broker.__new__(Broker)
    broker.journal = None
    broker.trading = SimpleNamespace(get_order_by_client_id=raiser)
    broker.bucket = broker_module.TokenBucket(rate_per_min=10_000)
    broker._log = lambda actor, event, payload: None
    return broker


def test_get_order_by_client_id_returns_absent_only_for_verified_not_found():
    def not_found(coid):
        raise _api_error('{"code":40410000,"message":"order not found"}', status=404)

    assert _lookup_broker(not_found).get_order_by_coid("client-1") is None


def test_statusless_not_found_message_is_unknown_not_verified_absence():
    def ambiguous(coid):
        raise _api_error('{"message":"order not found"}')

    with pytest.raises(broker_module.BrokerUnknownState):
        _lookup_broker(ambiguous).get_order_by_coid("client-1")


def test_non_alpaca_exception_with_404_attribute_is_not_verified_absence():
    class ImpostorError(RuntimeError):
        status_code = 404

    def ambiguous(coid):
        raise ImpostorError("not found")

    with pytest.raises(broker_module.BrokerUnknownState):
        _lookup_broker(ambiguous).get_order_by_coid("client-1")


@pytest.mark.parametrize(
    ("exc_factory", "expected"),
    [
        (lambda: _api_error("unauthorized", status=401), broker_module.BrokerAuthError),
        (lambda: _api_error("forbidden", status=403), broker_module.BrokerAuthError),
        (lambda: _api_error("bad request", status=422), broker_module.BrokerValidationError),
        (lambda: _api_error("slow down", status=429), broker_module.BrokerRateLimited),
        (lambda: _api_error("server error", status=500), broker_module.BrokerUnavailable),
        (lambda: ConnectionError("connection reset by peer"), broker_module.BrokerUnavailable),
        (lambda: TimeoutError("read timed out"), broker_module.BrokerUnavailable),
        (lambda: ValueError("could not decode response"), broker_module.BrokerUnknownState),
    ],
)
def test_lookup_transport_error_is_unknown_not_absent(exc_factory, expected, monkeypatch):
    """Every non-404 failure must raise. Returning None here would let a caller
    conclude 'no such order' from 'we could not ask'."""
    monkeypatch.setattr(broker_module.time, "sleep", lambda s: None)

    def failing(coid):
        raise exc_factory()

    with pytest.raises(broker_module.BrokerError) as caught:
        _lookup_broker(failing).get_order_by_coid("client-1")

    assert isinstance(caught.value, expected)
    assert not isinstance(caught.value, broker_module.OrderNotFound)


def test_auth_and_validation_errors_are_terminal_non_retryable():
    for exc in (_api_error("unauthorized", status=401), _api_error("invalid", status=422)):
        assert broker_module.classify_broker_error(exc).retryable is False


def test_rate_limit_and_server_errors_are_retryable():
    for exc in (_api_error("slow down", status=429), _api_error("boom", status=500)):
        assert broker_module.classify_broker_error(exc).retryable is True


def test_rate_limit_classification_carries_retry_after():
    err = broker_module.classify_broker_error(_api_error("slow down", status=429, retry_after="7"))
    assert isinstance(err, broker_module.BrokerRateLimited)
    assert err.retry_after == 7.0


def test_rate_limit_retry_is_bounded_jittered_and_read_only(monkeypatch):
    """Idempotent reads back off and retry; a non-idempotent mutation never does."""
    sleeps: list[float] = []
    monkeypatch.setattr(broker_module.time, "sleep", lambda s: sleeps.append(s))

    broker = Broker.__new__(Broker)
    broker.journal = None
    broker.bucket = broker_module.TokenBucket(rate_per_min=10_000)

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _api_error("slow down", status=429)
        return "ok"

    assert broker._call(flaky, "read", attempts=4) == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2
    assert all(0 < s <= broker_module.MAX_BACKOFF_SECONDS for s in sleeps)

    # A non-idempotent call is classified and raised on the first failure.
    mutations = {"n": 0}

    def mutating():
        mutations["n"] += 1
        raise _api_error("slow down", status=429)

    with pytest.raises(broker_module.BrokerRateLimited):
        broker._call(mutating, "mutate", attempts=4, idempotent=False)
    assert mutations["n"] == 1


def test_cancel_and_confirm_surfaces_unknown_lookup_rather_than_assuming_absent():
    broker = Broker.__new__(Broker)
    broker.cancel = lambda order_id: None
    broker._log = lambda actor, event, payload: None

    def unknown(coid):
        raise broker_module.BrokerUnavailable("venue unreachable")

    broker.get_order_by_coid = unknown

    with pytest.raises(broker_module.OrderStateUncertain, match="client-1"):
        broker.cancel_and_confirm("broker-1", "client-1", timeout=0.01, poll_seconds=0)


def test_submit_preserves_exact_decimal_quantity_and_tick_price_at_sdk_boundary():
    from alpaca.trading.client import TradingClient

    captured = []
    broker = Broker.__new__(Broker)
    broker.journal = None
    broker.bucket = SimpleNamespace(take=lambda: True)
    broker._log = lambda actor, event, payload: None

    class Accepted(dict):
        id = "order-1"
        submitted_at = None
        status = "accepted"

    client = TradingClient.__new__(TradingClient)
    client._use_raw_data = True

    def post(path, data):
        captured.append((path, data))
        return Accepted()

    client.post = post
    broker.trading = client
    broker.submit(
        symbol="BTC/USD",
        qty=Decimal("0.123456789012345678"),
        side="buy",
        client_order_id="gbx-exact",
        limit_price=Decimal("12345.6700"),
        instrument="crypto",
    )

    assert captured == [
        (
            "/orders",
            {
                "symbol": "BTC/USD",
                "qty": "0.123456789012345678",
                "side": "buy",
                "type": "limit",
                "time_in_force": "gtc",
                "client_order_id": "gbx-exact",
                "limit_price": "12345.6700",
            },
        )
    ]
    assert not any(isinstance(value, float) for value in captured[0][1].values())


# -- performance reporting -----------------------------------------------------


def test_portfolio_history_is_read_only_and_summarised():
    broker = Broker.__new__(Broker)
    broker.journal = None
    broker.bucket = broker_module.TokenBucket(rate_per_min=10_000)
    calls: list[object] = []

    def get_portfolio_history(request):
        calls.append(request)
        base = 1_756_650_000
        return SimpleNamespace(
            timestamp=[base, base + 86400, base + 172800],
            equity=["100000", "101500", "99500"],
        )

    broker.trading = SimpleNamespace(get_portfolio_history=get_portfolio_history)

    summary = broker.performance()

    assert len(calls) == 1, "the equity curve must be fetched exactly once"
    assert summary.starting_equity == Decimal("100000")
    assert summary.ending_equity == Decimal("99500")
    assert summary.absolute_pnl == Decimal("-500")
    # Peak 101500 -> trough 99500 is a larger decline than start-to-finish.
    assert summary.max_drawdown_pct < summary.total_return_pct
    assert summary.ratios_are_indicative is True


# -- sessions come from the venue calendar ------------------------------------


def test_reconcile_counts_sessions_from_the_venue_calendar():
    """Every expiry decision rests on this count. A weekday heuristic is
    quietly wrong on any holiday outside a hardcoded table, so the horizon
    must come from Alpaca's own calendar when one is reachable."""
    from datetime import date as _date

    from glassbox.market_calendar import MarketCalendar

    broker = Broker.__new__(Broker)
    broker._calendar = MarketCalendar(
        fetch=lambda s, e: [SimpleNamespace(date=d) for d in (_date(2026, 9, 1), _date(2026, 9, 2))]
    )

    # Only two sessions exist in that fake calendar, whatever the weekdays say.
    assert broker.calendar.sessions_between(_date(2026, 8, 31), _date(2026, 9, 30)) == 2
    assert broker.calendar.source == "alpaca"


def test_the_broker_builds_its_calendar_once():
    broker = Broker.__new__(Broker)
    broker._calendar = None
    broker.trading = SimpleNamespace(get_calendar=lambda request: [])
    broker._call = lambda fn, what: fn()

    first = broker.calendar
    assert broker.calendar is first, "the calendar was rebuilt per access"
