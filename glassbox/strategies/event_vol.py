"""Calendar-triggered volatility strategy.

This is the thesis of the whole project, expressed deterministically:

    When a scheduled catalyst falls inside an option's life, and implied
    volatility is cheap relative to what the underlying has actually been
    realising, buy defined-risk convexity and flatten after the event.

Deliberately NOT an LLM decision. The flagship trade of the competition cannot
depend on a model volunteering the right idea on the right afternoon -- it runs
on the calendar, emits a normal TradePlan, and goes through the same kernel as
everything else. The LLM's role is to confirm or veto, never to originate.

The expiry choice is the interesting part. See `select_expiry`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from .. import config as C
from ..macro import MacroEvent, next_event, sessions_remaining_at_measurement
from ..schema import OptionLeg, TradePlan


@dataclass(frozen=True)
class ExpiryQuote:
    """One candidate expiry, as observed in the chain."""
    expiry: date
    atm_iv: Decimal          # implied vol, e.g. Decimal("0.132")
    atm_straddle_px: Decimal  # per share
    bid_ask_pct: Decimal     # relative spread at the money, e.g. 0.04 = 4%


@dataclass(frozen=True)
class ExpiryChoice:
    expiry: date
    reason: str
    gamma_per_dollar: Decimal
    event_premium_vol_pts: Decimal


def select_expiry(
    candidates: list[ExpiryQuote],
    event: MacroEvent,
    measurement: datetime,
    baseline_iv: Decimal | None = None,
    max_spread_pct: Decimal | None = None,
    max_event_premium_pts: Decimal = Decimal("0.020"),
) -> ExpiryChoice | None:
    """Pick the expiry using the term structure, not an argument.

    Two forces pull in opposite directions and only data resolves them:

      Holiday/weekend discount -- options are priced in business time, so an
      expiry spanning a long weekend (Labor Day, 7 Sep) is cheap relative to
      its calendar length. Gamma per dollar scales as 1/T, so a shorter expiry
      buys materially more convexity per dollar.

      Event premium and post-print vol crush -- the market knows the release is
      scheduled. Short-dated options spanning a known catalyst carry an event
      premium, and once the number is out that premium collapses. The crush
      hits the SHORTEST expiry hardest, which is exactly the one the holiday
      discount is pushing us toward.

    So: prefer the shortest expiry that still (a) has real extrinsic value at
    measurement, (b) quotes tightly enough to mark honestly on an indicative
    feed, and (c) is NOT carrying an unusual event premium relative to the
    longer-dated baseline.

    Returns None if nothing qualifies -- refusing to trade is a valid outcome.
    """
    if not candidates:
        return None
    if max_spread_pct is None:
        max_spread_pct = C.MAX_ATM_SPREAD_PCT

    ordered = sorted(candidates, key=lambda c: c.expiry)

    # Baseline = the longest expiry still inside our measurement window, taken
    # as the "clean" vol level against which shorter-dated event premium is
    # measured. It must come from inside the window, or the premium figure that
    # lands in the journal is quoted against an expiry we never considered.
    in_window = [o for o in ordered
                 if C.OPTION_MIN_DTE_AT_MEASUREMENT
                 <= sessions_remaining_at_measurement(o.expiry, measurement)
                 <= C.OPTION_MAX_SESSIONS_AT_MEASUREMENT]
    if baseline_iv is not None:
        base = baseline_iv
    elif in_window:
        base = in_window[-1].atm_iv
    else:
        return None

    viable: list[tuple[Decimal, ExpiryQuote, Decimal]] = []

    for q in ordered:
        tdays = sessions_remaining_at_measurement(q.expiry, measurement)

        # (a) must still be alive at measurement, with real time value left.
        # A 0DTE contract marks off a stub quote in the widest window of the
        # week -- that is gambling on a mark, not on a market.
        if tdays < C.OPTION_MIN_DTE_AT_MEASUREMENT:
            continue
        if tdays > C.OPTION_MAX_SESSIONS_AT_MEASUREMENT:
            continue

        # (b) must quote tightly enough that the mark means something.
        if q.bid_ask_pct > max_spread_pct:
            continue

        # (c) must not be carrying an outsized event premium.
        premium = q.atm_iv - base
        if premium > max_event_premium_pts:
            continue

        # Convexity per dollar. Cost scales sqrt(T) and gamma scales 1/sqrt(T),
        # so payoff per dollar on a gap goes as 1/T. Expressed relative to the
        # longest candidate so the number is readable in a journal entry.
        # Reference = the longest expiry still inside our window, i.e. the one
        # a team without this analysis would default to.
        t_ref = max((sessions_remaining_at_measurement(o.expiry, measurement)
                     for o in ordered
                     if sessions_remaining_at_measurement(o.expiry, measurement)
                     <= C.OPTION_MAX_SESSIONS_AT_MEASUREMENT), default=1)
        gpd = Decimal(t_ref) / Decimal(max(tdays, 1))
        viable.append((gpd, q, premium))

    if not viable:
        return None

    gpd, best, premium = max(viable, key=lambda v: v[0])
    return ExpiryChoice(
        expiry=best.expiry,
        gamma_per_dollar=gpd,
        event_premium_vol_pts=premium,
        reason=(
            f"{best.expiry} carries {gpd:.2f}x the gap convexity per dollar of the "
            f"longest candidate, quotes at {best.bid_ask_pct:.1%} wide, and shows "
            f"{premium:+.1%} vol of event premium against a {base:.1%} baseline"
        ),
    )


def _occ(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    return f"{underlying}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"


def _round_strike(px: Decimal, increment: Decimal = Decimal("1")) -> Decimal:
    return (px / increment).quantize(Decimal("1")) * increment


@dataclass
class ChainLeg:
    """A concrete quotable contract, as seen in the chain."""
    symbol: str
    strike: Decimal
    right: str
    ask: Decimal
    bid: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.ask + self.bid) / 2


class EventVolStrategy:
    """Emits at most one plan per catalyst. Never sizes above the kernel's caps."""

    def __init__(self, underlying: str = "SPY"):
        self.underlying = underlying

    def propose(
        self,
        now: datetime,
        spot: Decimal,
        iv_vs_rv: Decimal,
        expiry_candidates: list[ExpiryQuote],
        chain: dict[date, list[ChainLeg]],
        measurement: datetime,
        remaining_budget: Decimal,
        already_positioned_for: set[str] | None = None,
    ) -> list[TradePlan]:
        """Return zero or one TradePlan. Zero is a normal, healthy outcome."""
        done = already_positioned_for or set()

        event = next_event(now, tier=C.EVENT_MIN_TIER,
                           within_hours=C.EVENT_LOOKAHEAD_HOURS)
        if event is None or event.name in done:
            return []

        # Only buy convexity when it is actually cheap. If the underlying is
        # already realising more than the options imply, we are not being paid
        # to own gamma and we stand down.
        if iv_vs_rv > C.MAX_IV_TO_RV_RATIO:
            return []

        choice = select_expiry(expiry_candidates, event, measurement)
        if choice is None:
            return []

        legs_available = chain.get(choice.expiry, [])
        if not legs_available:
            return []

        # Strangle rather than straddle: ~45% of the cost for ~2.2x the
        # contracts, which is the correct shape when the payoff is a step
        # function rather than linear in return.
        offset = spot * C.STRANGLE_OTM_PCT
        call_target = _round_strike(spot + offset)
        put_target = _round_strike(spot - offset)

        call = self._nearest(legs_available, "C", call_target)
        put = self._nearest(legs_available, "P", put_target)
        if call is None or put is None:
            return []

        # Marketable limit with a tolerance band -- we are pricing off an
        # indicative feed, so pay up slightly rather than sit unfilled, but
        # never chase beyond the band.
        call_limit = (call.ask * (1 + C.LIMIT_TOLERANCE)).quantize(Decimal("0.01"))
        put_limit = (put.ask * (1 + C.LIMIT_TOLERANCE)).quantize(Decimal("0.01"))

        pair_cost = (call_limit + put_limit) * 100
        if pair_cost <= 0:
            return []

        budget = min(remaining_budget, C.EVENT_TRADE_DAILY_CAP)
        qty = int(budget / pair_cost)
        if qty < 1:
            return []

        premium = pair_cost * qty
        plan_id = str(uuid4())

        return [TradePlan(
            plan_id=plan_id,
            sleeve="convex",
            action="open",
            instrument="option",
            symbol=self.underlying,
            side="buy",
            is_event_trade=True,
            option_legs=[
                OptionLeg(symbol=call.symbol, side="buy", qty=qty, limit_price=call_limit),
                OptionLeg(symbol=put.symbol, side="buy", qty=qty, limit_price=put_limit),
            ],
            notional_usd=premium,
            max_loss_usd=premium,          # exact: long premium only
            time_exit=measurement,
            thesis=(
                f"{event.name} releases {event.when:%Y-%m-%d %H:%M} ET, "
                f"{event.hours_until(now):.0f}h before the account is measured. "
                f"Implied volatility is {iv_vs_rv:.2f}x realised, so convexity is "
                f"cheap into a scheduled catalyst. Buying a {C.STRANGLE_OTM_PCT:.1%} "
                f"strangle for the move, not the direction. {choice.reason}."
            ),
            evidence=[
                f"macro_cal: {event.name} {event.when:%Y-%m-%d %H:%M} ET ({event.source})",
                f"iv_to_rv_ratio_{self.underlying.lower()}={iv_vs_rv:.3f}",
                f"expiry_selected={choice.expiry} gamma_per_dollar={choice.gamma_per_dollar:.2f}x",
                f"event_premium_vol_pts={choice.event_premium_vol_pts:+.4f}",
                f"spot={spot} call_strike={call.strike} put_strike={put.strike}",
                f"premium_at_risk={premium} max_loss={premium}",
            ],
            confidence=0.6,
        )]

    @staticmethod
    def _nearest(legs: list[ChainLeg], right: str, target: Decimal) -> ChainLeg | None:
        same = [l for l in legs if l.right == right and l.ask > 0]
        return min(same, key=lambda l: abs(l.strike - target)) if same else None
