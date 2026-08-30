"""Alpaca wrapper. The broker is always the source of truth.

Three things this file exists to guarantee:

  1. The process cannot boot against a live account.
  2. We never exceed the rate limit, across every loop, from one shared budget.
  3. Local state is never trusted. Every tick re-reads positions and orders
     from Alpaca and rebuilds the picture from scratch. If our view and the
     broker's view disagree, the broker wins -- always, without exception.

(3) is what makes a crash cost minutes instead of the week.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar

from . import config as C
from . import env
from .ids import EVENT_PREFIX as EVENT_COID_PREFIX
from .kernel import PortfolioState, Position
from .macro import trading_days_between
from .schema import OptionContract

log = logging.getLogger("glassbox.broker")

# An order in one of these states can no longer consume buying power.
_TERMINAL_ORDER_STATES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "suspended",
    "done_for_day",
    "replaced",
}

T = TypeVar("T")


class NotPaperTrading(RuntimeError):
    """Raised at startup. Deliberately fatal."""


class OrderStateUncertain(RuntimeError):
    """A cancellation could not be observed in a terminal broker state."""


def _order_status(order: Any) -> str:
    """Normalize Alpaca enum and string order statuses."""
    return str(getattr(order, "status", "")).lower().split(".")[-1]


class TokenBucket:
    """Shared across every loop. 150/min against Alpaca's 200 ceiling."""

    def __init__(self, rate_per_min: int = C.RATE_LIMIT_PER_MIN):
        self.capacity = float(rate_per_min)
        self.tokens = float(rate_per_min)
        self.refill_per_sec = rate_per_min / 60.0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self._last) * self.refill_per_sec
                )
                self._last = now
                if self.tokens >= n:
                    self.tokens -= n
                    return True
                shortfall = n - self.tokens
                wait = shortfall / self.refill_per_sec
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 1.0))


# -- typed failure classification ---------------------------------------------
#
# The distinction this section exists to protect: "the venue answered, and that
# order does not exist" is a fact. "We could not reach the venue" is an absence
# of information. Collapsing the second into the first is what lets a caller
# conclude an order was never placed when it may be live and filling.


class BrokerError(RuntimeError):
    """Base for a classified Alpaca failure."""

    #: Safe to retry, but only for an idempotent operation.
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class OrderNotFound(BrokerError):
    """Verified absence: the venue responded 404. The order does not exist."""


class BrokerAuthError(BrokerError):
    """401/403. Terminal -- the same credentials cannot succeed on a retry."""


class BrokerValidationError(BrokerError):
    """400/422. Terminal -- the request itself was refused."""


class BrokerRateLimited(BrokerError):
    """429. Retryable for idempotent operations, honouring Retry-After."""

    retryable = True


class BrokerUnavailable(BrokerError):
    """5xx, connection failure, or timeout. Retryable for idempotent reads."""

    retryable = True


class BrokerUnknownState(BrokerError):
    """Unclassifiable or malformed. Never retried, never read as absence."""


MAX_BACKOFF_SECONDS = 8.0

_NOT_FOUND_MARKERS = ("not found", "does not exist")
_UNAVAILABLE_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "network",
    "unreachable",
    "reset by peer",
    "temporarily",
    "unavailable",
)


def _is_api_error(exc: Exception) -> bool:
    """True for an Alpaca APIError, i.e. the venue answered with an error."""
    return any(base.__name__ == "APIError" for base in type(exc).__mro__)


def _status_code_of(exc: Exception) -> int | None:
    """APIError.status_code is None unless an HTTP error was attached."""
    try:
        code = getattr(exc, "status_code", None)
    except Exception:  # a property that raises is not a status code
        return None
    return code if isinstance(code, int) else None


def _retry_after_of(exc: Exception) -> float | None:
    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def classify_broker_error(exc: Exception) -> BrokerError:
    """Map any exception raised by the SDK onto exactly one typed outcome.

    Anything that cannot be positively identified becomes BrokerUnknownState,
    which is neither retryable nor absence. Unknown fails closed.
    """
    if isinstance(exc, BrokerError):
        return exc

    text = str(exc).lower()
    code = _status_code_of(exc)
    detail = str(exc)

    if code == 404:
        return OrderNotFound(detail, status_code=404)
    # An APIError carrying a not-found message is still the venue answering,
    # even when no HTTP response object was attached to it.
    if code is None and _is_api_error(exc) and any(m in text for m in _NOT_FOUND_MARKERS):
        return OrderNotFound(detail)
    if code in (401, 403):
        return BrokerAuthError(detail, status_code=code)
    if code in (400, 422):
        return BrokerValidationError(detail, status_code=code)
    if code == 429:
        return BrokerRateLimited(detail, status_code=429, retry_after=_retry_after_of(exc))
    if code is not None and 500 <= code <= 599:
        return BrokerUnavailable(detail, status_code=code)
    if code is None and any(m in text for m in _UNAVAILABLE_MARKERS):
        return BrokerUnavailable(detail)
    return BrokerUnknownState(detail, status_code=code)


def _is_retryable(exc: Exception) -> bool:
    return classify_broker_error(exc).retryable


class Broker:
    def __init__(self, journal=None, bucket: TokenBucket | None = None):
        # Every read goes through env.clean(). systemd's EnvironmentFile parser
        # keeps inline '#' comments that python-dotenv strips, and load_dotenv()
        # will not override what systemd already set -- so a value that looks
        # fine by hand can arrive here with a comment glued to it. See
        # glassbox/env.py for the full account of what that broke.
        key = env.get("ALPACA_API_KEY")
        secret = env.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")

        # Fatal, and deliberately checked three ways. A live account here would
        # be an unrecoverable mistake, so the cost of a false positive is zero
        # and the cost of a false negative is everything.
        if env.get("ALPACA_PAPER_TRADE", "true").lower() != "true":
            raise NotPaperTrading("ALPACA_PAPER_TRADE is not true")
        if not key.startswith("PK"):
            raise NotPaperTrading("API key is not a paper key (expected PK prefix)")
        base = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if "paper-api" not in base:
            raise NotPaperTrading(f"base URL is not the paper endpoint: {base}")

        from alpaca.data.historical.crypto import CryptoHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        # Alpaca's published return annotations include a dictionary fallback
        # for most endpoints. This wrapper validates attributes at the boundary,
        # so keep the untrusted SDK clients dynamic inside this module.
        self.trading: Any = TradingClient(key, secret, paper=True)
        self.data: Any = StockHistoricalDataClient(key, secret)
        # Alpaca serves crypto from its own venue and its own data client.
        # Without this the crypto sleeve can never be priced, and therefore
        # never trades.
        self.crypto_data: Any = CryptoHistoricalDataClient(key, secret)
        self.bucket = bucket or TokenBucket()
        self.journal = journal
        # require_choice, not getenv: an unrecognised ALPACA_ENV must crash, not
        # quietly take the dev branch and skip the scored-account guards below.
        self.env = env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")

    # -- plumbing --------------------------------------------------------------

    def _call(
        self,
        fn: Callable[[], T],
        what: str,
        attempts: int = 4,
        *,
        idempotent: bool = True,
    ) -> T:
        """Rate-limited, with bounded jittered backoff.

        Retries are for idempotent operations only. A non-idempotent call is
        classified and re-raised on its first failure: replaying a mutation
        whose outcome is unknown is how one intent becomes two orders. Submit
        does not come through here at all -- see `submit`.
        """
        last: BrokerError | None = None
        for i in range(attempts):
            if not self.bucket.take():
                raise RuntimeError(f"rate limit budget exhausted calling {what}")
            try:
                return fn()
            except Exception as exc:
                error = classify_broker_error(exc)
                last = error
                if not idempotent or not error.retryable or i == attempts - 1:
                    raise error from exc
                wait = min(2.0**i, MAX_BACKOFF_SECONDS)
                if error.retry_after is not None:
                    wait = min(max(error.retry_after, 0.0), MAX_BACKOFF_SECONDS)
                # Jitter so that several loops recovering from one 429 do not
                # resynchronise and reproduce the burst that caused it.
                wait = random.uniform(wait / 2.0, wait) if wait > 0 else 0.0
                log.warning("%s failed (%s), retrying in %.2fs", what, error, wait)
                time.sleep(wait)
        if last is None:  # pragma: no cover - the loop always runs at least once
            raise RuntimeError(f"no attempt made calling {what}")
        raise last  # pragma: no cover

    def _log(self, actor: str, event: str, payload: dict) -> None:
        if self.journal is not None:
            self.journal.append(actor, event, payload)

    # -- startup ---------------------------------------------------------------

    def assert_ready(self) -> dict:
        """Print the account identity into the journal at every startup.

        This is how you prove on Friday which account was traded, and how you
        catch 'submitted from the wrong account' before it costs the week.
        """
        acct = self._call(self.trading.get_account, "get_account")
        actual_account_id = str(acct.account_number)
        expected_account_id = env.expected_account_id(self.env)
        if actual_account_id != expected_account_id:
            raise RuntimeError(
                f"Alpaca returned account {actual_account_id}, expected "
                f"{expected_account_id} for ALPACA_ENV={self.env}; refusing "
                "to continue"
            )
        info = {
            "env": self.env,
            "account_number": actual_account_id,
            "status": str(acct.status),
            "equity": str(acct.equity),
            "cash": str(acct.cash),
            "options_level": getattr(acct, "options_trading_level", None),
            "paper": True,
        }
        if getattr(acct, "trading_blocked", False):
            raise RuntimeError("trading is blocked on this account")
        if self.env == "scored":
            if Decimal(str(acct.equity)) != C.STARTING_EQUITY:
                raise RuntimeError(
                    f"scored account equity is {acct.equity}, expected exactly {C.STARTING_EQUITY}"
                )
            if self.positions():
                raise RuntimeError("scored account is not clean: positions exist")
        self._log("broker", "STARTUP", info)
        return info

    # -- reads -----------------------------------------------------------------

    def account(self):
        return self._call(self.trading.get_account, "get_account")

    def clock(self):
        return self._call(self.trading.get_clock, "get_clock")

    def positions(self) -> list:
        return self._call(self.trading.get_all_positions, "get_all_positions")

    def open_orders(self) -> list:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        return self._call(lambda: self.trading.get_orders(req), "get_orders")

    def orders_today(self) -> list:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, after=start, limit=500)
        return self._call(lambda: self.trading.get_orders(req), "get_orders_today")

    def snapshot_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        from alpaca.data.requests import StockSnapshotRequest

        equities = [s for s in symbols if "/" not in s]
        out: dict[str, Decimal] = {}
        if equities:
            snaps = self._call(
                lambda: self.data.get_stock_snapshot(
                    StockSnapshotRequest(symbol_or_symbols=equities)
                ),
                "get_stock_snapshot",
            )
            for sym, s in snaps.items():
                px = None
                if getattr(s, "latest_trade", None):
                    px = Decimal(str(s.latest_trade.price))
                elif getattr(s, "latest_quote", None):
                    q = s.latest_quote
                    if q.bid_price and q.ask_price:
                        px = (Decimal(str(q.bid_price)) + Decimal(str(q.ask_price))) / 2
                if px:
                    out[sym] = px

        # AUDIT NOTE: crypto symbols were filtered out above and never priced
        # anywhere else, so CryptoStrategy always saw no price and returned no
        # plans. The whole sleeve -- and with it the "runs 24/7, operates while
        # the equity market is shut" claim -- was dead code. Alpaca serves
        # crypto from a different data client, so it needs its own request.
        cryptos = [s for s in symbols if "/" in s]
        if cryptos:
            try:
                from alpaca.data.requests import CryptoSnapshotRequest

                snaps = self._call(
                    lambda: self.crypto_data.get_crypto_snapshot(
                        CryptoSnapshotRequest(symbol_or_symbols=cryptos)
                    ),
                    "get_crypto_snapshot",
                )
                for sym, s in snaps.items():
                    px = None
                    if getattr(s, "latest_trade", None):
                        px = Decimal(str(s.latest_trade.price))
                    elif getattr(s, "latest_quote", None):
                        q = s.latest_quote
                        if q.bid_price and q.ask_price:
                            px = (Decimal(str(q.bid_price)) + Decimal(str(q.ask_price))) / 2
                    if px:
                        out[sym] = px
            except Exception as exc:
                # Never let a crypto data failure stop the equity path.
                log.warning("crypto snapshot failed: %s", exc)
        return out

    # -- reconciliation --------------------------------------------------------

    def reconcile(self, kill_switch_tripped: bool = False) -> PortfolioState:
        """Rebuild the whole picture from the broker. Trust nothing local."""
        acct = self.account()
        raw_positions = self.positions()
        todays = self.orders_today()
        clock = self.clock()

        positions: list[Position] = []
        convex_premium = Decimal(0)
        core_value = Decimal(0)
        core_cost = Decimal(0)

        for p in raw_positions:
            cls = str(getattr(p, "asset_class", "")).lower()
            sym = str(p.symbol)
            mv = Decimal(str(p.market_value or 0))
            qty = Decimal(str(p.qty or 0))
            cost = Decimal(str(getattr(p, "cost_basis", 0) or 0))

            if "option" in cls:
                try:
                    c = OptionContract.parse(sym)
                    underlying = c.underlying
                    right = c.right
                except ValueError:
                    underlying, right = sym[:3], "C"
                # Near-ATM approximation. The kernel's delta check is a sanity
                # bound, not the risk control, so this does not need to be exact.
                d = Decimal("0.5") if right == "C" else Decimal("-0.5")
                positions.append(
                    Position(
                        symbol=sym,
                        instrument="option",
                        qty=qty,
                        market_value=mv,
                        underlying=underlying,
                        net_delta_shares=d * qty * 100,
                        premium_paid=abs(cost),
                    )
                )
                if qty > 0:
                    convex_premium += abs(cost)
            elif "crypto" in cls:
                positions.append(
                    Position(
                        symbol=sym,
                        instrument="crypto",
                        qty=qty,
                        market_value=mv,
                        net_delta_shares=qty,
                    )
                )
            else:
                positions.append(
                    Position(
                        symbol=sym,
                        instrument="equity",
                        qty=qty,
                        market_value=mv,
                        net_delta_shares=qty,
                    )
                )
                core_value += mv
                core_cost += cost

        by_symbol: dict[str, int] = {}
        convex_today = Decimal(0)
        event_today = Decimal(0)
        open_coids: set[str] = set()
        today = datetime.now(timezone.utc).date()

        for o in todays:
            s = str(o.symbol)
            by_symbol[s] = by_symbol.get(s, 0) + 1
            coid = getattr(o, "client_order_id", None)
            if coid:
                open_coids.add(str(coid))
            # Premium committed today.
            #
            # AUDIT NOTE, two bugs fixed here, both of which let the agent spend
            # past its own caps:
            #
            #  1. Only FILLED orders were counted. An order that is submitted
            #     and still resting contributes real committed premium, but was
            #     invisible to invariants 03 and 04 -- so plans raced each
            #     other. Measured: six resting orders worth $21,000 reported as
            #     $0, and a seventh $7,000 plan approved on top.
            #  2. event_today was hard-coded to Decimal(0) below and never
            #     computed, which made EVENT_TRADE_DAILY_CAP a per-ORDER cap
            #     rather than a per-day one. Two $16,000 event strangles in one
            #     day both passed an $18,000 cap.
            #
            # Working orders are counted at their limit price, which is the
            # most we could pay for them. Better to over-count exposure we have
            # committed than to discover it after the fills arrive.
            if len(s) > 15 and str(o.side).lower().endswith("buy"):
                filled = Decimal(str(getattr(o, "filled_qty", 0) or 0))
                avg = Decimal(str(getattr(o, "filled_avg_price", 0) or 0))
                committed = Decimal(0)
                if filled and avg:
                    committed += filled * avg * 100
                status = str(getattr(o, "status", "")).lower().split(".")[-1]
                if status not in _TERMINAL_ORDER_STATES:
                    qty = Decimal(str(getattr(o, "qty", 0) or 0))
                    lim = Decimal(str(getattr(o, "limit_price", 0) or 0))
                    resting = qty - filled
                    if resting > 0 and lim > 0:
                        committed += resting * lim * 100
                convex_today += committed
                if str(coid or "").startswith(EVENT_COID_PREFIX):
                    event_today += committed

        # Trading-day counts for every plausible expiry. Invariant 10 refuses
        # any option plan whose expiry it cannot count sessions to, so an empty
        # map here silently refuses EVERY option trade -- which is exactly what
        # happened until the practice harness surfaced it.
        today = datetime.now(C.ET).date()
        horizon = {
            date.fromordinal(today.toordinal() + d): trading_days_between(
                today, date.fromordinal(today.toordinal() + d)
            )
            for d in range(1, 46)
        }

        state = PortfolioState(
            equity=Decimal(str(acct.equity)),
            cash=Decimal(str(acct.cash)),
            positions=positions,
            core_sleeve_value=core_value,
            core_sleeve_cost_basis=core_cost,
            convex_premium_outstanding=convex_premium,
            convex_premium_today=convex_today,
            event_premium_today=event_today,
            orders_today=len(todays),
            orders_today_by_symbol=by_symbol,
            open_client_order_ids=open_coids,
            trading_days_to=horizon,
            market_open=bool(clock.is_open),
            now_et=datetime.now(C.ET),
            # AUDIT NOTE: this used to ask only for symbols already HELD, plus
            # SPY and QQQ. Every strategy skips a symbol with no snapshot price
            # and invariants 02/05/12 hard-refuse without one, so any symbol
            # not already in the book could never be bought -- which meant it
            # never entered the book, which meant it was never priced. IWM sat
            # in that trap all week, leaving 30% of the core sleeve in cash,
            # and it closed off the other nine allowlisted names to any future
            # strategy. Price the whole tradeable universe.
            snapshot_price=self.snapshot_prices(
                sorted(
                    {(p.underlying or p.symbol) for p in positions}
                    | set(C.EQUITY_ALLOWLIST)
                    | set(C.OPTION_UNDERLYING_ALLOWLIST)
                    | set(C.CRYPTO_ALLOWLIST)
                )
            ),
            kill_switch_tripped=kill_switch_tripped,
        )
        self._log(
            "broker",
            "RECONCILED",
            {
                "equity": str(state.equity),
                "positions": len(positions),
                "convex_premium_outstanding": str(convex_premium),
                "orders_today": state.orders_today,
                "market_open": state.market_open,
            },
        )
        return state

    def portfolio_history(self, *, period: str = "1W", timeframe: str = "1D"):
        """Alpaca's own equity curve for the account. Read-only.

        The competition scores total account equity, so the scorer's number is
        the one worth reporting. Reconstructing equity from our fill log would
        be rebuilding a figure the venue already publishes, and any drift
        between the two would be ours to explain.
        """
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        request = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        return self._call(
            lambda: self.trading.get_portfolio_history(request),
            "get_portfolio_history",
        )

    def performance(self, *, period: str = "1W", timeframe: str = "1D"):
        """Equity curve reduced to a reportable, caveated summary."""
        from .performance import summarize_history

        return summarize_history(self.portfolio_history(period=period, timeframe=timeframe))

    # -- writes ----------------------------------------------------------------

    def submit(
        self,
        *,
        symbol: str,
        qty: Decimal | int,
        side: str,
        client_order_id: str,
        limit_price: Decimal | None = None,
        instrument: str = "equity",
    ) -> Any:
        """Submit one order. Never retried.

        A retry on submit is only safe because client_order_id is deterministic
        -- Alpaca rejects the duplicate. We still do not retry here, because a
        timeout leaves the outcome genuinely unknown and the correct response is
        to reconcile and look, not to guess.
        """
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        s = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if instrument == "crypto" else TimeInForce.DAY

        req: Any
        if limit_price is not None:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=float(qty),
                side=s,
                time_in_force=tif,
                limit_price=float(limit_price),
                client_order_id=client_order_id,
            )
        else:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=float(qty),
                side=s,
                time_in_force=tif,
                client_order_id=client_order_id,
            )

        if not self.bucket.take():
            raise RuntimeError("rate limit budget exhausted on submit")

        self._log(
            "broker",
            "ORDER_SUBMITTING",
            {
                "symbol": symbol,
                "qty": str(qty),
                "side": side,
                "limit_price": str(limit_price) if limit_price else None,
                "client_order_id": client_order_id,
            },
        )

        order: Any = self.trading.submit_order(req)

        # Broker-side identity and timestamp. This is the part of the journal a
        # third party can verify, because we do not control either value.
        self._log(
            "broker",
            "ORDER_ACCEPTED",
            {
                "client_order_id": client_order_id,
                "broker_order_id": str(order.id),
                "broker_submitted_at": str(getattr(order, "submitted_at", None)),
                "status": str(order.status),
            },
        )
        return order

    def get_order_by_coid(self, coid: str):
        """Return the order, or None only when the venue verified its absence.

        Every other failure raises a typed BrokerError. A caller that cannot
        distinguish "no such order" from "could not ask" will eventually submit
        a second order for an intent that already has one.
        """
        try:
            return self._call(
                lambda: self.trading.get_order_by_client_id(coid),
                "get_order_by_client_id",
                attempts=3,
            )
        except OrderNotFound:
            return None

    def cancel(self, order_id: str) -> None:
        self._call(lambda: self.trading.cancel_order_by_id(order_id), "cancel_order")
        self._log("broker", "ORDER_CANCEL_REQUESTED", {"broker_order_id": str(order_id)})

    def cancel_and_confirm(
        self,
        order_id: str,
        client_order_id: str,
        *,
        timeout: float = 15.0,
        poll_seconds: float = 0.5,
    ) -> Any:
        """Request cancellation and return only after terminal broker state."""
        cancel_error: str | None = None
        try:
            self.cancel(order_id)
        except Exception as exc:
            # The order may already be terminal. The read below, not the cancel
            # response, is authoritative.
            cancel_error = str(exc)

        deadline = time.monotonic() + timeout
        last_status = "not_found"
        lookup_error: str | None = None
        while True:
            try:
                order = self.get_order_by_coid(client_order_id)
            except BrokerError as exc:
                # Could not ask. That is not evidence of cancellation, so keep
                # polling and let the deadline decide.
                lookup_error = str(exc)
                last_status = "unknown"
                order = None
            else:
                lookup_error = None
            if order is not None:
                last_status = _order_status(order)
                if last_status in _TERMINAL_ORDER_STATES:
                    self._log(
                        "broker",
                        "ORDER_CANCEL_CONFIRMED",
                        {
                            "broker_order_id": str(order_id),
                            "client_order_id": client_order_id,
                            "status": last_status,
                            "filled_qty": str(getattr(order, "filled_qty", 0) or 0),
                            "cancel_error": cancel_error,
                        },
                    )
                    return order
            if time.monotonic() >= deadline:
                detail = {
                    "broker_order_id": str(order_id),
                    "client_order_id": client_order_id,
                    "last_status": last_status,
                    "cancel_error": cancel_error,
                    "lookup_error": lookup_error,
                }
                self._log("broker", "ORDER_CANCEL_UNCERTAIN", detail)
                raise OrderStateUncertain(
                    f"order {client_order_id} did not reach terminal state "
                    f"within {timeout}s (last status {last_status})"
                )
            time.sleep(poll_seconds)

    def close_position(self, symbol: str) -> Any:
        order = self._call(
            lambda: self.trading.close_position(symbol),
            "close_position",
            idempotent=False,
        )
        self._log("broker", "POSITION_CLOSED", {"symbol": symbol})
        return order
