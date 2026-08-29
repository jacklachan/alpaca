"""The core sleeve. Deliberately passive.

Fixed-weight, bought once at Monday's open, held to the measurement. No
signals, no rebalancing, no cleverness.

This is a decision, not an omission, and it is worth defending out loud:

  We have no demonstrable edge in four days of directional equity trading. A
  deterministic rules engine on 26 market hours of IEX data would produce
  noise dressed as strategy, and the journal would record it as reasoning. The
  honest thing is to say we declined to pretend to an edge we do not have, and
  to spend the risk budget where we can actually articulate why we expect to
  be paid -- scheduled volatility events.

The core sleeve's job is to hold the account up while the convex sleeve takes
the shots. Passive discharges that job perfectly and cannot break at 03:00.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .. import config as C
from ..kernel import PortfolioState
from ..schema import TradePlan

# Fixed weights of the core sleeve. Broad beta, most liquid instruments on the
# free feed, no single-name idiosyncratic risk.
# Weights are bounded by the kernel, not by taste. Invariant 05 caps a single
# underlying at 25% of equity, so on a $65k sleeve against $100k of equity no
# weight may exceed ~38%. An earlier 50/30/20 split put SPY at 32.5% of equity
# and the kernel refused it -- correctly. Three broad ETFs, no single-name
# idiosyncratic risk, every leg comfortably inside the limit.
WEIGHTS: dict[str, Decimal] = {
    "SPY": Decimal("0.35"),   # 22.75% of starting equity
    "QQQ": Decimal("0.35"),   # 22.75%
    "IWM": Decimal("0.30"),   # 19.50%
}

# A disaster stop, not a trading stop. Wide enough that ordinary noise never
# touches it; present so max_loss is computable and invariant 02 is satisfied.
DISASTER_STOP_PCT = Decimal("0.05")


class CoreStrategy:
    """Buys the allocation once, then does nothing for the rest of the week."""

    name = "core"

    def propose_from_state(self, state: PortfolioState,
                           positioned_for: set[str] | None = None) -> list[TradePlan]:
        if not state.market_open:
            return []

        held = {p.symbol for p in state.positions if p.instrument == "equity"}
        plans: list[TradePlan] = []

        for symbol, weight in WEIGHTS.items():
            if symbol in held:
                continue                      # bought already; never top up
            spot = state.snapshot_price.get(symbol)
            if not spot or spot <= 0:
                continue

            notional = (C.CORE_SLEEVE_USD * weight).quantize(Decimal("1"))
            if notional <= 0:
                continue

            stop = (spot * (1 - DISASTER_STOP_PCT)).quantize(Decimal("0.01"))
            qty = notional / spot
            max_loss = (abs(spot - stop) * qty * C.GAP_MULTIPLIER).quantize(Decimal("1"))

            plans.append(TradePlan(
                sleeve="core", action="open", instrument="equity", symbol=symbol,
                side="buy", notional_usd=notional, max_loss_usd=max_loss,
                stop=stop,
                thesis=(
                    f"Core sleeve allocation: {weight:.0%} of a "
                    f"${C.CORE_SLEEVE_USD:,.0f} passive book in {symbol}, bought "
                    f"once and held to the measurement. We claim no directional "
                    f"edge over four sessions and do not trade this sleeve; it "
                    f"finances the convex sleeve and holds the account up."),
                evidence=[
                    f"allocation_policy=core_passive weight={weight}",
                    f"spot={spot}",
                    f"disaster_stop={stop} ({DISASTER_STOP_PCT:.0%} below spot)",
                    "edge_claimed=none",
                ],
                confidence=0.5,
            ))
        return plans
