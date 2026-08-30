"""Account performance measured the way the competition measures it.

The scoring window is judged on **total account equity**, not on a tally of
closed-trade P&L, so that is what this module reads: Alpaca's own portfolio
history, which already accounts for open positions, unrealised marks, and fees.
Deriving equity from our own fill log instead would be reconstructing a number
the venue already publishes -- and disagreeing with the scorer's number is a
losing position to be in.

Risk-adjusted ratios are included because a return figure alone cannot
distinguish a steady curve from one that got lucky after nearly blowing up.
But they carry an explicit honesty constraint, which is the whole reason this
module has a `reliable` flag rather than just printing a number:

    A Sharpe ratio computed from four daily observations is not a Sharpe
    ratio. It is noise with a Greek letter attached.

A seven-day hackathon yields ~5 daily points. Annualising that is arithmetic,
not evidence. So every ratio is returned alongside the sample size that
produced it, `ratios_are_indicative` is true until there are enough
observations to mean anything, and the dashboard is expected to render the
caveat rather than hide it. Reporting an impressive-looking Sharpe from five
points would be exactly the kind of claim the rest of this repository exists to
refuse.

Money stays Decimal. The statistics are float on purpose: these are reporting
figures, never executable prices, and no order size is derived from them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

#: Below this many return observations, annualised ratios are indicative only.
MIN_OBSERVATIONS_FOR_RATIO = 20

#: US equity trading days per year, for annualising daily observations.
TRADING_DAYS_PER_YEAR = 252

#: Assume a flat cash rate of zero. Over a one-week window the risk-free
#: adjustment is smaller than the rounding, and inventing a rate would add a
#: parameter nobody can check.
RISK_FREE_RATE = 0.0


@dataclass(frozen=True)
class EquityPoint:
    """One observation of total account equity."""

    at: datetime
    equity: Decimal

    @property
    def equity_float(self) -> float:
        return float(self.equity)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    """Sample standard deviation. Zero for fewer than two points."""
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    variance = sum((v - average) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _downside_stdev(values: Sequence[float], target: float = 0.0) -> float:
    """Deviation of the losing observations only.

    Sortino's point is that upside volatility is not risk. Uses the full count
    as the denominator, which is the standard definition -- dividing by only
    the losing count would flatter a strategy that rarely loses but loses big.
    """
    if len(values) < 2:
        return 0.0
    shortfalls = [min(0.0, v - target) ** 2 for v in values]
    return math.sqrt(sum(shortfalls) / (len(values) - 1))


def period_returns(points: Sequence[EquityPoint]) -> list[float]:
    """Simple period-over-period returns. Skips non-positive equity."""
    out: list[float] = []
    for previous, current in zip(points, points[1:]):
        start = previous.equity_float
        if start <= 0:
            continue
        out.append((current.equity_float - start) / start)
    return out


def max_drawdown(points: Sequence[EquityPoint]) -> tuple[float, datetime | None]:
    """Largest peak-to-trough decline, and when the trough happened."""
    peak = float("-inf")
    worst = 0.0
    worst_at: datetime | None = None
    for point in points:
        value = point.equity_float
        peak = max(peak, value)
        if peak <= 0:
            continue
        decline = (value - peak) / peak
        if decline < worst:
            worst, worst_at = decline, point.at
    return worst, worst_at


def sharpe(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualised Sharpe. Zero when there is no dispersion to divide by."""
    if len(returns) < 2:
        return 0.0
    excess = [r - RISK_FREE_RATE / periods_per_year for r in returns]
    deviation = _stdev(excess)
    if deviation == 0.0:
        return 0.0
    return (_mean(excess) / deviation) * math.sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualised Sortino. Zero when nothing lost, since there is no downside
    deviation to divide by -- an infinite ratio is not a useful report."""
    if len(returns) < 2:
        return 0.0
    excess = [r - RISK_FREE_RATE / periods_per_year for r in returns]
    downside = _downside_stdev(excess)
    if downside == 0.0:
        return 0.0
    return (_mean(excess) / downside) * math.sqrt(periods_per_year)


@dataclass(frozen=True)
class PerformanceSummary:
    """What the account did, and how much to trust the ratios."""

    starting_equity: Decimal
    ending_equity: Decimal
    absolute_pnl: Decimal
    total_return_pct: float
    max_drawdown_pct: float
    max_drawdown_at: datetime | None
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    volatility_pct: float
    best_period_pct: float
    worst_period_pct: float
    observations: int
    ratios_are_indicative: bool
    window_start: datetime | None = None
    window_end: datetime | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "starting_equity": str(self.starting_equity),
            "ending_equity": str(self.ending_equity),
            "absolute_pnl": str(self.absolute_pnl),
            "total_return_pct": round(self.total_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown_at": self.max_drawdown_at.isoformat() if self.max_drawdown_at else None,
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "volatility_pct": round(self.volatility_pct, 4),
            "best_period_pct": round(self.best_period_pct, 4),
            "worst_period_pct": round(self.worst_period_pct, 4),
            "observations": self.observations,
            "ratios_are_indicative": self.ratios_are_indicative,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "notes": list(self.notes),
        }


EMPTY = PerformanceSummary(
    starting_equity=Decimal(0),
    ending_equity=Decimal(0),
    absolute_pnl=Decimal(0),
    total_return_pct=0.0,
    max_drawdown_pct=0.0,
    max_drawdown_at=None,
    sharpe_ratio=0.0,
    sortino_ratio=0.0,
    calmar_ratio=0.0,
    volatility_pct=0.0,
    best_period_pct=0.0,
    worst_period_pct=0.0,
    observations=0,
    ratios_are_indicative=True,
    notes=("no equity history available",),
)


def summarize(
    points: Sequence[EquityPoint],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> PerformanceSummary:
    """Reduce an equity curve to a reportable summary.

    Total return is measured on equity endpoints, which is how the competition
    scores it. Ratios are computed from period returns and flagged indicative
    until the sample is large enough to carry meaning.
    """
    points = [p for p in points if p.equity is not None]
    if not points:
        return EMPTY

    starting = points[0].equity
    ending = points[-1].equity
    pnl = ending - starting
    total_return = float(pnl / starting) if starting > 0 else 0.0

    returns = period_returns(points)
    drawdown, drawdown_at = max_drawdown(points)
    deviation = _stdev(returns)

    # Annualise the realised return before comparing it with drawdown, so
    # Calmar is not a one-week number divided by a one-week drawdown.
    annualised = 0.0
    if returns:
        annualised = _mean(returns) * periods_per_year
    calmar = annualised / abs(drawdown) if drawdown < 0 else 0.0

    indicative = len(returns) < MIN_OBSERVATIONS_FOR_RATIO
    notes: list[str] = []
    if indicative:
        notes.append(
            f"{len(returns)} return observations is below the {MIN_OBSERVATIONS_FOR_RATIO} "
            "needed for an annualised ratio to carry meaning; ratios are indicative only"
        )
    if drawdown == 0.0 and returns:
        notes.append("no peak-to-trough decline observed in this window")
    if not returns:
        notes.append("a single equity observation cannot produce a return series")

    return PerformanceSummary(
        starting_equity=starting,
        ending_equity=ending,
        absolute_pnl=pnl,
        total_return_pct=total_return * 100.0,
        max_drawdown_pct=drawdown * 100.0,
        max_drawdown_at=drawdown_at,
        sharpe_ratio=sharpe(returns, periods_per_year),
        sortino_ratio=sortino(returns, periods_per_year),
        calmar_ratio=calmar,
        volatility_pct=deviation * math.sqrt(periods_per_year) * 100.0,
        best_period_pct=(max(returns) * 100.0) if returns else 0.0,
        worst_period_pct=(min(returns) * 100.0) if returns else 0.0,
        observations=len(returns),
        ratios_are_indicative=indicative,
        window_start=points[0].at,
        window_end=points[-1].at,
        notes=tuple(notes),
    )


def points_from_portfolio_history(history: Any) -> list[EquityPoint]:
    """Convert Alpaca's PortfolioHistory into equity points, defensively.

    Alpaca returns parallel arrays and can include null equity entries for
    periods with no data. A null is dropped rather than carried forward: an
    invented observation would change every ratio computed from it.
    """
    timestamps = list(getattr(history, "timestamp", None) or [])
    equities = list(getattr(history, "equity", None) or [])

    out: list[EquityPoint] = []
    for stamp, equity in zip(timestamps, equities):
        if equity is None:
            continue
        try:
            value = Decimal(str(equity))
        except Exception:
            continue
        if isinstance(stamp, datetime):
            at = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        else:
            try:
                at = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
            except Exception:
                continue
        out.append(EquityPoint(at=at, equity=value))
    out.sort(key=lambda p: p.at)
    return out


def summarize_history(history: Any, **kwargs: Any) -> PerformanceSummary:
    """One call from an Alpaca PortfolioHistory to a reportable summary."""
    return summarize(points_from_portfolio_history(history), **kwargs)


def equity_curve_json(points: Iterable[EquityPoint]) -> list[dict[str, Any]]:
    """Serialise a curve for the dashboard, smallest useful shape."""
    return [{"at": p.at.isoformat(), "equity": str(p.equity)} for p in points]
