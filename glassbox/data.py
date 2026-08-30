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
from statistics import median

from . import config as C
from . import env
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

    def __init__(self, broker, cache_seconds: int = 30):
        self.broker = broker
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[datetime, object]] = {}

    def _cached(self, key: str, fn):
        now = datetime.now(timezone.utc)
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
        def fetch():

            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionChainRequest

            client = OptionHistoricalDataClient(
                env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
            )
            return self.broker._call(
                lambda: client.get_option_chain(OptionChainRequest(underlying_symbol=underlying)),
                f"chain:{underlying}",
            )

        return self._cached(f"chain:{underlying}", fetch)

    # -- shaping for the strategy ---------------------------------------------

    def expiry_quotes(
        self, underlying: str, spot: Decimal, band: float = 0.01
    ) -> list[ExpiryQuote]:
        """ATM implied vol and quote width per expiry -- the term structure the
        expiry selector consumes."""
        chain = self.option_chain(underlying)
        rows: dict[date, list[tuple[float, float, float]]] = {}

        for sym, c in chain.items():
            iv = getattr(c, "implied_volatility", None)
            if iv is None:
                continue
            parsed = _parse_occ(sym)
            if parsed is None:
                continue
            exp, _, strike = parsed
            if abs(strike - float(spot)) / float(spot) >= band:
                continue
            spread = float("nan")
            q = getattr(c, "latest_quote", None)
            mid = float("nan")
            if q is not None:
                bid = float(getattr(q, "bid_price", 0) or 0)
                ask = float(getattr(q, "ask_price", 0) or 0)
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                    spread = (ask - bid) / mid
            rows.setdefault(exp, []).append((float(iv), spread, mid))

        out: list[ExpiryQuote] = []
        for exp, vals in sorted(rows.items()):
            ivs = [v for v, _, _ in vals]
            spreads = [s for _, s, _ in vals if s == s]
            mids = [m for _, _, m in vals if m == m]
            out.append(
                ExpiryQuote(
                    expiry=exp,
                    atm_iv=Decimal(str(round(sum(ivs) / len(ivs), 4))),
                    atm_straddle_px=Decimal(str(round(median(mids) * 2, 2)))
                    if mids
                    else Decimal(0),
                    bid_ask_pct=Decimal(str(round(median(spreads), 4)))
                    if spreads
                    else Decimal("1"),
                )
            )  # no quote -> unusable, filter rejects it
        return out

    def chain_legs(
        self, underlying: str, expiry: date, spot: Decimal, band: float = 0.05
    ) -> dict[date, list[ChainLeg]]:
        """Quotable contracts near the money for one expiry."""
        chain = self.option_chain(underlying)
        legs: list[ChainLeg] = []
        for sym, c in chain.items():
            parsed = _parse_occ(sym)
            if parsed is None:
                continue
            exp, right, strike = parsed
            if exp != expiry:
                continue
            if abs(strike - float(spot)) / float(spot) > band:
                continue
            q = getattr(c, "latest_quote", None)
            if q is None:
                continue
            bid = Decimal(str(getattr(q, "bid_price", 0) or 0))
            ask = Decimal(str(getattr(q, "ask_price", 0) or 0))
            if ask <= 0:
                continue
            legs.append(
                ChainLeg(symbol=sym, strike=Decimal(str(strike)), right=right, ask=ask, bid=bid)
            )
        return {expiry: legs}

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


def _parse_occ(sym: str) -> tuple[date, str, float] | None:
    try:
        root = "".join(ch for ch in sym[:6] if ch.isalpha())
        rest = sym[len(root) :]
        exp = date(2000 + int(rest[0:2]), int(rest[2:4]), int(rest[4:6]))
        return exp, rest[6], int(rest[7:]) / 1000
    except Exception:
        return None
