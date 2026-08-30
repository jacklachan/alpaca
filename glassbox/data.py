"""Market data and the feature layer.

Pure functions where possible, so features are unit-testable without a network.
No LLM here -- this is what turns raw prices into the small table of meaningful
numbers the model is later handed.

One honest naming note. The original plan had a feature called `iv_rank`, an
implied-vol percentile against a trailing year. That is not computable on the
free tier: there is no IV history, only a current chain. Shipping a number
called `iv_rank` that is not one would put a fabricated metric into the journal
as cited evidence -- exactly the failure the journal exists to prevent. What we
can compute honestly is the ratio of implied to recently realised volatility,
and that is what the strategy actually uses.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from . import config as C
from .candidates import ActiveOptionContract
from .option_data import OptionDataGateway
from .strategies.event_vol import ChainLeg, ExpiryQuote

log = logging.getLogger("glassbox.data")


# --- features (pure) ----------------------------------------------------------


def realised_vol(closes: list[float], periods_per_year: int = 252) -> float:
    """Annualised close-to-close volatility. Needs at least three closes."""
    if len(closes) < 3:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def iv_to_rv(implied: float, realised: float) -> float:
    """How expensive is convexity right now.

    Below ~1.0 the options are cheap against what the underlying is actually
    doing. Above ~1.35 we are paying up for gamma and stand down.
    """
    if realised <= 0:
        return float("inf")
    return implied / realised


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    tail = trs[-n:]
    return sum(tail) / len(tail) if tail else 0.0


def overnight_gap(prev_close: float, today_open: float) -> float:
    return (today_open - prev_close) / prev_close if prev_close else 0.0


# --- market data --------------------------------------------------------------


class MarketData:
    """Everything the strategies need, cached briefly to respect the budget."""

    def __init__(
        self,
        broker,
        cache_seconds: int = 30,
        *,
        option_data_client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.broker = broker
        self.cache_seconds = cache_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache: dict[str, tuple[datetime, object]] = {}
        self.options = OptionDataGateway(
            broker,
            cache_seconds,
            option_data_client=option_data_client,
            clock=self.clock,
        )

    def _cached(self, key: str, fn):
        now = self.clock()
        hit = self._cache.get(key)
        if hit and (now - hit[0]).total_seconds() < self.cache_seconds:
            return hit[1]
        val = fn()
        self._cache[key] = (now, val)
        return val

    def daily_closes(self, symbol: str, days: int = 30) -> list[float]:
        def fetch():
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            req = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=datetime.now(timezone.utc) - timedelta(days=days * 2),
            )
            bars = self.broker._call(lambda: self.broker.data.get_stock_bars(req), f"bars:{symbol}")
            data = bars.data.get(symbol, []) if hasattr(bars, "data") else []
            return [float(b.close) for b in data][-days:]

        return self._cached(f"closes:{symbol}:{days}", fetch)

    def realised_vol(self, symbol: str, days: int = 10) -> float:
        return realised_vol(self.daily_closes(symbol, days + 5)[-(days + 1) :])

    def option_chain(self, underlying: str) -> dict:
        return self.options.option_chain(underlying)

    def active_option_contracts(
        self, underlying: str, expiry: date
    ) -> dict[str, ActiveOptionContract]:
        return self.options.active_contracts(underlying, expiry)

    # -- shaping for the strategy ---------------------------------------------

    def expiry_quotes(
        self, underlying: str, spot: Decimal, band: float = 0.01
    ) -> list[ExpiryQuote]:
        return self.options.expiry_quotes(underlying, spot, band)

    def chain_legs(
        self, underlying: str, expiry: date, spot: Decimal, band: float = 0.05
    ) -> dict[date, list[ChainLeg]]:
        return self.options.chain_legs(underlying, expiry, spot, band)

    def feature_table(self, symbols: list[str], state) -> dict:
        """The small table of meaningful numbers handed to the model.

        Every value here is something the model may cite as evidence, so every
        value must be one we actually computed.
        """
        out: dict[str, dict] = {}
        for sym in symbols:
            try:
                closes = self.daily_closes(sym, 25)
                if len(closes) < 5:
                    continue
                rv10 = realised_vol(closes[-11:])
                rv5 = realised_vol(closes[-6:])
                spot = state.snapshot_price.get(sym)
                row = {
                    "spot": float(spot) if spot else closes[-1],
                    "realised_vol_10d": round(rv10, 4),
                    "realised_vol_5d": round(rv5, 4),
                    "ma5_state": "above" if closes[-1] > sum(closes[-5:]) / 5 else "below",
                    "ret_5d": round((closes[-1] / closes[-6] - 1), 4) if len(closes) > 5 else 0.0,
                }
                if sym in C.OPTION_UNDERLYING_ALLOWLIST and spot:
                    quotes = self.expiry_quotes(sym, spot)
                    if quotes:
                        front = min(quotes, key=lambda q: q.expiry)
                        row["implied_vol_front"] = float(front.atm_iv)
                        row["iv_to_rv_ratio"] = round(iv_to_rv(float(front.atm_iv), rv10), 3)
                out[sym] = row
            except Exception as exc:
                log.warning("features failed for %s: %s", sym, exc)
        return out
