"""Deterministic option-surface analysis. No model, no network, no I/O.

A long strangle is a bet that the underlying moves more than the option market
has priced. Buying one without looking at the surface is buying a lottery
ticket at whatever price the counter asks, and the two ways it quietly fails
are both visible in the Greeks before the order is sent:

  * **The position is not the trade you think it is.** A "long strangle" that
    is net short gamma or net short vega is mislabelled. That can happen from a
    sign error, a leg that failed to fill, or a strike chosen so far out that
    its contribution is negligible. Checking the aggregate rather than trusting
    the label is how that gets caught.

  * **The convexity is too expensive to hold.** Theta is the rent. If the
    position burns a large fraction of its premium per day, the underlying has
    to move soon *and* far, and an event two days out cannot pay for four days
    of decay. Equally, if implied volatility is already elevated going in, the
    post-event IV collapse can lose money on a correct directional call --
    which is the classic way an event-volatility trade is right and still
    loses.

So this module turns the surface into deterministic, quotable refusals. Every
threshold is a named constant, every verdict carries its reason, and none of it
is advisory: the gate returns a refusal that the caller must honour. The model
never sees any of it, because none of it is a decision the model is allowed to
make.

Greeks arrive from Alpaca's option snapshot. When they are missing the answer
is abstention, not a guess -- an unpriced convexity trade is exactly the one
not to take.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

#: Share-equivalents per option contract.
CONTRACT_MULTIPLIER = Decimal(100)

#: A long-convexity position burning more than this share of its premium per
#: day needs the move to arrive almost immediately to pay for itself.
#:
#: Kept for the record, and still reported as evidence, but no longer the gate.
#: A DAILY rate limit silently assumes a one-day horizon, and this strategy
#: deliberately buys the shortest expiry that survives to measurement because
#: that is where gamma per dollar is highest. Short-dated options always burn a
#: large share of premium per day -- that is the structure, not a defect -- so
#: the daily cap refused precisely what the expiry selector was chosen to find.
#: Two gates pulling in opposite directions, and the position never traded.
MAX_DAILY_THETA_BURN_PCT = Decimal("0.12")

#: What the gate actually asks: how much of the premium is certain to decay
#: before the account is valued.
#:
#: A third is the most we will pay in guaranteed decay to reach a catalyst.
#: Past that the structure is mismatched to the horizon -- you are buying an
#: option whose life is mostly spent before the thing you bought it for.
#:
#: This is a genuine relaxation for short holds and a tightening for long ones:
#: at the old 12%/day a four-day hold implied a 48% allowance, which this
#: refuses. It is not a licence to buy decay, and the breakeven-move gate below
#: still has to pass independently.
MAX_HOLD_THETA_BURN_PCT = Decimal("0.333")

#: Implied volatility above this is expensive convexity: the post-event
#: collapse can lose money on a correct directional call.
MAX_ENTRY_IMPLIED_VOL = Decimal("0.85")

#: A strangle whose breakeven needs a larger move than this is priced for an
#: event bigger than the one we are trading.
MAX_BREAKEVEN_MOVE_PCT = Decimal("0.06")

#: Below this the leg contributes nothing and the "strangle" is really a
#: single-sided bet wearing two legs.
MIN_ABS_LEG_DELTA = Decimal("0.02")


def _decimal(value: object, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


@dataclass(frozen=True)
class LegGreeks:
    """The surface for one contract, as the venue reported it."""

    symbol: str
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    implied_volatility: Decimal
    rho: Decimal = Decimal(0)

    @classmethod
    def from_snapshot(cls, symbol: str, snapshot: Any) -> LegGreeks | None:
        """Build from an Alpaca OptionsSnapshot, or None if it is unusable.

        Returning None rather than zeros is deliberate: a zero delta is a
        meaningful value and must never stand in for a missing one.
        """
        greeks = getattr(snapshot, "greeks", None)
        if greeks is None:
            return None
        delta = _decimal(getattr(greeks, "delta", None))
        gamma = _decimal(getattr(greeks, "gamma", None))
        theta = _decimal(getattr(greeks, "theta", None))
        vega = _decimal(getattr(greeks, "vega", None))
        if None in (delta, gamma, theta, vega):
            return None
        iv = _decimal(getattr(snapshot, "implied_volatility", None), Decimal(0))
        return cls(
            symbol=symbol,
            delta=delta,  # type: ignore[arg-type]
            gamma=gamma,  # type: ignore[arg-type]
            theta=theta,  # type: ignore[arg-type]
            vega=vega,  # type: ignore[arg-type]
            implied_volatility=iv or Decimal(0),
            rho=_decimal(getattr(greeks, "rho", None), Decimal(0)) or Decimal(0),
        )


@dataclass(frozen=True)
class PositionGreeks:
    """Signed aggregate exposure for a whole multi-leg position."""

    delta: Decimal = Decimal(0)
    gamma: Decimal = Decimal(0)
    theta: Decimal = Decimal(0)
    vega: Decimal = Decimal(0)
    max_implied_vol: Decimal = Decimal(0)
    legs_priced: int = 0

    @property
    def is_long_gamma(self) -> bool:
        return self.gamma > 0

    @property
    def is_long_vega(self) -> bool:
        return self.vega > 0

    def as_dict(self) -> dict[str, str]:
        return {
            "delta": str(self.delta),
            "gamma": str(self.gamma),
            "theta": str(self.theta),
            "vega": str(self.vega),
            "max_implied_vol": str(self.max_implied_vol),
            "legs_priced": str(self.legs_priced),
        }


def aggregate(legs: Iterable[Any], greeks: Mapping[str, LegGreeks]) -> PositionGreeks | None:
    """Sum signed, contract-scaled exposure across the legs.

    Returns None if any leg is unpriced: a partial aggregate would understate
    exposure precisely where it matters.
    """
    total_delta = total_gamma = total_theta = total_vega = Decimal(0)
    highest_iv = Decimal(0)
    counted = 0

    for leg in legs:
        symbol = getattr(leg, "symbol", "")
        surface = greeks.get(symbol)
        if surface is None:
            return None
        sign = Decimal(1) if getattr(leg, "side", "buy") == "buy" else Decimal(-1)
        size = Decimal(str(getattr(leg, "qty", 0))) * CONTRACT_MULTIPLIER * sign
        total_delta += surface.delta * size
        total_gamma += surface.gamma * size
        total_theta += surface.theta * size
        total_vega += surface.vega * size
        highest_iv = max(highest_iv, surface.implied_volatility)
        counted += 1

    if counted == 0:
        return None
    return PositionGreeks(
        delta=total_delta,
        gamma=total_gamma,
        theta=total_theta,
        vega=total_vega,
        max_implied_vol=highest_iv,
        legs_priced=counted,
    )


def daily_theta_burn_pct(position: PositionGreeks, premium_paid: Decimal) -> Decimal:
    """Share of premium lost per day to decay, as a positive fraction."""
    if premium_paid <= 0:
        return Decimal(0)
    return abs(position.theta) / premium_paid


def breakeven_move_pct(
    *, premium_paid: Decimal, spot: Decimal, contracts: Decimal
) -> Decimal | None:
    """How far the underlying must travel for a long strangle to break even.

    Approximate by construction -- it ignores the strike gap -- and deliberately
    conservative: it overstates the required move, so it can only ever refuse a
    trade this metric would otherwise allow.
    """
    if spot <= 0 or contracts <= 0:
        return None
    per_share = premium_paid / (contracts * CONTRACT_MULTIPLIER)
    return per_share / spot


@dataclass(frozen=True)
class GreeksVerdict:
    """Deterministic accept or refuse, with the reason attached."""

    approved: bool
    reason: str
    position: PositionGreeks | None = None
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "position": self.position.as_dict() if self.position else None,
        }


def assess_long_convexity(
    legs: Iterable[Any],
    greeks: Mapping[str, LegGreeks],
    *,
    premium_paid: Decimal,
    spot: Decimal,
    contracts: Decimal,
    hold_days: Decimal | None = None,
    max_daily_theta_burn: Decimal = MAX_DAILY_THETA_BURN_PCT,
    max_hold_theta_burn: Decimal = MAX_HOLD_THETA_BURN_PCT,
    max_implied_vol: Decimal = MAX_ENTRY_IMPLIED_VOL,
    max_breakeven_move: Decimal = MAX_BREAKEVEN_MOVE_PCT,
) -> GreeksVerdict:
    """Gate a long-convexity option position against its own surface."""
    legs = list(legs)
    position = aggregate(legs, greeks)
    if position is None:
        return GreeksVerdict(
            False,
            "option surface unavailable for at least one leg",
            None,
            ("abstained rather than sizing an unpriced convexity trade",),
        )

    evidence: list[str] = [
        f"net delta {position.delta}, gamma {position.gamma}, "
        f"theta {position.theta}, vega {position.vega}"
    ]

    if not position.is_long_gamma:
        return GreeksVerdict(
            False, f"position is not net long gamma ({position.gamma})", position, tuple(evidence)
        )
    if not position.is_long_vega:
        return GreeksVerdict(
            False, f"position is not net long vega ({position.vega})", position, tuple(evidence)
        )

    for leg in legs:
        surface = greeks.get(getattr(leg, "symbol", ""))
        if surface is not None and abs(surface.delta) < MIN_ABS_LEG_DELTA:
            return GreeksVerdict(
                False,
                f"leg {surface.symbol} contributes almost nothing "
                f"(|delta| {abs(surface.delta)} < {MIN_ABS_LEG_DELTA})",
                position,
                tuple(evidence),
            )

    if position.max_implied_vol > max_implied_vol:
        return GreeksVerdict(
            False,
            f"implied volatility {position.max_implied_vol} above the "
            f"{max_implied_vol} entry ceiling; convexity is priced rich",
            position,
            tuple(evidence),
        )

    burn = daily_theta_burn_pct(position, premium_paid)
    evidence.append(f"daily theta burn {burn:.4f} of premium")

    # Judge the decay we will actually pay, over the days we will actually
    # hold, rather than a daily rate against a one-day horizon we do not have.
    hold = hold_days if hold_days is not None and hold_days > 0 else Decimal(1)
    hold_burn = burn * hold
    evidence.append(f"decay to measurement {hold_burn:.4f} of premium over {hold:.2f}d")
    if hold_burn > max_hold_theta_burn:
        return GreeksVerdict(
            False,
            f"decay to measurement {hold_burn:.4f} of premium over {hold:.2f}d "
            f"exceeds {max_hold_theta_burn}",
            position,
            tuple(evidence),
        )

    move = breakeven_move_pct(premium_paid=premium_paid, spot=spot, contracts=contracts)
    if move is None:
        return GreeksVerdict(False, "cannot compute a breakeven move", position, tuple(evidence))
    evidence.append(f"breakeven move {move:.4f} of spot")
    if move > max_breakeven_move:
        return GreeksVerdict(
            False,
            f"breakeven move {move:.4f} exceeds {max_breakeven_move}",
            position,
            tuple(evidence),
        )

    return GreeksVerdict(
        True,
        "long gamma and vega, decay and breakeven within limits",
        position,
        tuple(evidence),
    )
