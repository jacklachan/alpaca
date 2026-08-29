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
import os
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar

from . import config as C
from . import env
from .kernel import PortfolioState, Position
from .macro import trading_days_between
from .schema import OptionContract

log = logging.getLogger("glassbox.broker")

T = TypeVar("T")


class NotPaperTrading(RuntimeError):
    """Raised at startup. Deliberately fatal."""


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
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self._last) * self.refill_per_sec)
                self._last = now
                if self.tokens >= n:
                    self.tokens -= n
                    return True
                shortfall = n - self.tokens
                wait = shortfall / self.refill_per_sec
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 1.0))


_RETRYABLE = ("429", "500", "502", "503", "504", "timeout", "timed out",
              "connection", "temporarily")


def _is_retryable(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(t in s for t in _RETRYABLE)


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

        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.stock import StockHistoricalDataClient

        self.trading = TradingClient(key, secret, paper=True)
        self.data = StockHistoricalDataClient(key, secret)
        self.bucket = bucket or TokenBucket()
        self.journal = journal
        # require_choice, not getenv: an unrecognised ALPACA_ENV must crash, not
        # quietly take the dev branch and skip the scored-account guards below.
        self.env = env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")

    # -- plumbing --------------------------------------------------------------

    def _call(self, fn: Callable[[], T], what: str, attempts: int = 4) -> T:
        """Rate-limited, retried with backoff. Never retries a submit -- see
        `submit` for why that would be unsafe without idempotency."""
        last: Exception | None = None
        for i in range(attempts):
            if not self.bucket.take():
                raise RuntimeError(f"rate limit budget exhausted calling {what}")
            try:
                return fn()
            except Exception as exc:
                last = exc
                if not _is_retryable(exc) or i == attempts - 1:
                    raise
                wait = 2 ** i
                log.warning("%s failed (%s), retrying in %ss", what, exc, wait)
                time.sleep(wait)
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
        info = {
            "env": self.env,
            "account_number": str(acct.account_number),
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
                    f"scored account equity is {acct.equity}, expected exactly "
                    f"{C.STARTING_EQUITY}")
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
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        return self._call(lambda: self.trading.get_orders(req), "get_orders")

    def orders_today(self) -> list:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
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
                    StockSnapshotRequest(symbol_or_symbols=equities)),
                "get_stock_snapshot")
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
                    underlying, expiry = c.underlying, c.expiry
                    right = c.right
                except ValueError:
                    underlying, expiry, right = sym[:3], None, "C"
                # Near-ATM approximation. The kernel's delta check is a sanity
                # bound, not the risk control, so this does not need to be exact.
                d = Decimal("0.5") if right == "C" else Decimal("-0.5")
                positions.append(Position(
                    symbol=sym, instrument="option", qty=qty, market_value=mv,
                    underlying=underlying,
                    net_delta_shares=d * qty * 100,
                    premium_paid=abs(cost)))
                if qty > 0:
                    convex_premium += abs(cost)
            elif "crypto" in cls:
                positions.append(Position(symbol=sym, instrument="crypto", qty=qty,
                                          market_value=mv, net_delta_shares=qty))
            else:
                positions.append(Position(symbol=sym, instrument="equity", qty=qty,
                                          market_value=mv, net_delta_shares=qty))
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
            # Premium committed today, counted from filled option buys.
            if len(s) > 15 and str(o.side).lower().endswith("buy"):
                filled = Decimal(str(getattr(o, "filled_qty", 0) or 0))
                avg = Decimal(str(getattr(o, "filled_avg_price", 0) or 0))
                if filled and avg:
                    convex_today += filled * avg * 100

        # Trading-day counts for every plausible expiry. Invariant 10 refuses
        # any option plan whose expiry it cannot count sessions to, so an empty
        # map here silently refuses EVERY option trade -- which is exactly what
        # happened until the practice harness surfaced it.
        today = datetime.now(C.ET).date()
        horizon = {
            date.fromordinal(today.toordinal() + d): trading_days_between(
                today, date.fromordinal(today.toordinal() + d))
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
            snapshot_price=self.snapshot_prices(
                sorted({(p.underlying or p.symbol) for p in positions}
                       | {"SPY", "QQQ"})),
            kill_switch_tripped=kill_switch_tripped,
        )
        self._log("broker", "RECONCILED", {
            "equity": str(state.equity), "positions": len(positions),
            "convex_premium_outstanding": str(convex_premium),
            "orders_today": state.orders_today, "market_open": state.market_open,
        })
        return state

    # -- writes ----------------------------------------------------------------

    def submit(self, *, symbol: str, qty: Decimal | int, side: str,
               client_order_id: str, limit_price: Decimal | None = None,
               instrument: str = "equity") -> Any:
        """Submit one order. Never retried.

        A retry on submit is only safe because client_order_id is deterministic
        -- Alpaca rejects the duplicate. We still do not retry here, because a
        timeout leaves the outcome genuinely unknown and the correct response is
        to reconcile and look, not to guess.
        """
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        s = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if instrument == "crypto" else TimeInForce.DAY

        if limit_price is not None:
            req = LimitOrderRequest(symbol=symbol, qty=float(qty), side=s,
                                    time_in_force=tif,
                                    limit_price=float(limit_price),
                                    client_order_id=client_order_id)
        else:
            req = MarketOrderRequest(symbol=symbol, qty=float(qty), side=s,
                                     time_in_force=tif,
                                     client_order_id=client_order_id)

        if not self.bucket.take():
            raise RuntimeError("rate limit budget exhausted on submit")

        self._log("broker", "ORDER_SUBMITTING", {
            "symbol": symbol, "qty": str(qty), "side": side,
            "limit_price": str(limit_price) if limit_price else None,
            "client_order_id": client_order_id})

        order = self.trading.submit_order(req)

        # Broker-side identity and timestamp. This is the part of the journal a
        # third party can verify, because we do not control either value.
        self._log("broker", "ORDER_ACCEPTED", {
            "client_order_id": client_order_id,
            "broker_order_id": str(order.id),
            "broker_submitted_at": str(getattr(order, "submitted_at", None)),
            "status": str(order.status)})
        return order

    def get_order_by_coid(self, coid: str):
        try:
            return self._call(
                lambda: self.trading.get_order_by_client_id(coid),
                "get_order_by_client_id", attempts=2)
        except Exception:
            return None

    def cancel(self, order_id: str) -> None:
        self._call(lambda: self.trading.cancel_order_by_id(order_id), "cancel_order")
        self._log("broker", "ORDER_CANCELLED", {"broker_order_id": str(order_id)})

    def close_position(self, symbol: str) -> Any:
        order = self._call(lambda: self.trading.close_position(symbol), "close_position")
        self._log("broker", "POSITION_CLOSED", {"symbol": symbol})
        return order
