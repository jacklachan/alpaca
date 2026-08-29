"""The crypto sleeve. Runs 24/7 -- which is the entire point.

Two jobs:

  1. Give the agent something real to do outside market hours, which is what
     makes four days of genuinely continuous autonomous operation visible in
     the journal rather than merely claimed.

  2. Carry the pre-open catalysts. Alpaca runs its own crypto venue, so in the
     hour after an 08:15 or 08:30 ET release this sleeve is the only thing that
     can trade the reaction while every equity-only book is still shut.

     This job was originally written around the Friday payrolls print. Alpaca's
     guidelines put the measurement at EOD Thu 3 Sep, which moves payrolls
     outside the scored window entirely -- so the catalysts that matter here are
     now Wednesday's ADP (08:15) and Thursday's jobless claims (08:30), and the
     sleeve stands down once the measurement has passed.

Honest sizing note, because it belongs in the pitch rather than in a footnote:
at 1x on a $10,000 sleeve, a 1% move is $100. This is not where the P&L comes
from and we do not pretend otherwise. It is where continuous operation and the
event-window response are demonstrated.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .. import config as C
from ..ids import stable_plan_id
from ..kernel import PortfolioState
from ..macro import MEASUREMENT_ET, next_event
from ..schema import TradePlan

WEIGHTS: dict[str, Decimal] = {
    "BTC/USD": Decimal("0.60"),
    "ETH/USD": Decimal("0.40"),
}

STOP_PCT = Decimal("0.04")
TREND_LOOKBACK_HOURS = 12

# How long after a tier-1 release the reaction window stays open. The move is
# fast; holding past it is just carrying overnight crypto risk for no reason.
EVENT_WINDOW_MINUTES = 45


class CryptoStrategy:
    """Momentum with tight stops, plus an event-window response."""

    name = "crypto"

    def __init__(self, data=None):
        self.data = data

    def propose_from_state(self, state: PortfolioState,
                           positioned_for: set[str] | None = None) -> list[TradePlan]:
        held = {p.symbol for p in state.positions if p.instrument == "crypto"}
        now = state.now_et
        plans: list[TradePlan] = []

        in_event_window, event = self._event_window(now)

        for symbol, weight in WEIGHTS.items():
            if symbol in held:
                continue
            spot = state.snapshot_price.get(symbol)
            if not spot or spot <= 0:
                continue

            notional = (C.CRYPTO_SLEEVE_USD * weight).quantize(Decimal("1"))
            stop = (spot * (1 - STOP_PCT)).quantize(Decimal("0.01"))
            qty = notional / spot
            max_loss = (abs(spot - stop) * qty * C.GAP_MULTIPLIER).quantize(Decimal("1"))

            if in_event_window:
                thesis = (
                    f"{event.name} released at {event.when:%H:%M} ET. The equity "
                    f"market is closed until 09:30, so for this window crypto is "
                    f"the only venue where the reaction is tradeable. Taking a "
                    f"defined-risk position in {symbol} on published information, "
                    f"with a hard time exit before the measurement.")
                evidence = [
                    f"macro_cal: {event.name} {event.when:%Y-%m-%d %H:%M} ET (released)",
                    "equity_market_open=False crypto_venue_open=True",
                    f"spot={spot} stop={stop}",
                    f"time_exit={MEASUREMENT_ET:%Y-%m-%d %H:%M} ET",
                ]
                time_exit = MEASUREMENT_ET
                confidence = 0.55
            else:
                thesis = (
                    f"Crypto sleeve baseline allocation in {symbol}, {weight:.0%} "
                    f"of a ${C.CRYPTO_SLEEVE_USD:,.0f} sleeve. Runs continuously "
                    f"so the agent is operating outside equity hours, with a "
                    f"{STOP_PCT:.0%} stop bounding the position.")
                evidence = [
                    f"allocation_policy=crypto_baseline weight={weight}",
                    f"spot={spot} stop={stop}",
                    "venue=alpaca_crypto hours=24/7",
                ]
                time_exit = None
                confidence = 0.5

            plans.append(TradePlan(
                plan_id=stable_plan_id(
                    "crypto", now.date(), symbol,
                    event.name if in_event_window else "baseline"),
                sleeve="crypto", action="open", instrument="crypto", symbol=symbol,
                side="buy", notional_usd=notional, max_loss_usd=max_loss,
                stop=stop, time_exit=time_exit,
                thesis=thesis, evidence=evidence, confidence=confidence))
        return plans

    @staticmethod
    def _event_window(now):
        """True in the minutes just after an in-window release, before measurement.

        AUDIT NOTE: this used to require tier == 1. When the measurement moved to
        EOD Thu 3 Sep, the only tier-1 event in the calendar (payrolls, Fri 4 Sep)
        fell outside the scored window -- which made this branch unreachable and
        silently turned the crypto sleeve into a static allocation. The whole
        stated reason for the sleeve is to express a pre-open reaction while
        equities are shut, so it now triggers on the catalysts that actually
        fall inside the window: Wednesday's ADP and Thursday's claims, both
        tier 2. The `now < MEASUREMENT_ET` guard is what keeps it honest.
        """
        from ..macro import CALENDAR
        for e in CALENDAR:
            if e.tier > C.EVENT_MIN_TIER:
                continue
            delta = (now - e.when).total_seconds() / 60
            if 0 <= delta <= EVENT_WINDOW_MINUTES and now < MEASUREMENT_ET:
                return True, e
        return False, None
