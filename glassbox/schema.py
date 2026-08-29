"""The trade-plan schema.

This is load-bearing, not cosmetic: it is what turns "the LLM returned some
text" into "the LLM returned a validated object, or nothing at all".

Design note -- the schema is deliberately PERMISSIVE about dangerous plans.
A short option leg validates here and is refused by the kernel. That is on
purpose: if the schema rejected it, a hostile plan would die as a Pydantic
error with no reason string and no journal entry. Hostile plans must be
*representable* so they can be *visibly refused*.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ids import stable_plan_id

Sleeve = Literal["core", "crypto", "convex"]
Instrument = Literal["equity", "crypto", "option"]
Side = Literal["buy", "sell"]

# OCC 21-char: root(6, space-padded) YYMMDD C|P strike(8, price*1000)
OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<exp>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


class OptionContract(BaseModel):
    """Parsed view of an OCC option symbol."""

    symbol: str
    underlying: str
    expiry: date
    right: Literal["C", "P"]
    strike: Decimal

    @classmethod
    def parse(cls, symbol: str) -> "OptionContract":
        m = OCC_RE.match(symbol.strip().upper().replace(" ", ""))
        if not m:
            raise ValueError(f"not a valid OCC option symbol: {symbol!r}")
        exp = m.group("exp")
        return cls(
            symbol=symbol.strip().upper(),
            underlying=m.group("root"),
            expiry=date(2000 + int(exp[0:2]), int(exp[2:4]), int(exp[4:6])),
            right=m.group("right"),  # type: ignore[arg-type]
            strike=Decimal(m.group("strike")) / 1000,
        )


class OptionLeg(BaseModel):
    """One option leg. `side` is intentionally unconstrained -- see module docstring."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="OCC 21-character contract symbol")
    side: Side
    qty: int = Field(gt=0, le=200, description="contracts")
    limit_price: Decimal | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def _occ_shaped(cls, v: str) -> str:
        OptionContract.parse(v)  # raises if malformed
        return v.strip().upper()

    @property
    def contract(self) -> OptionContract:
        return OptionContract.parse(self.symbol)

    @property
    def is_long(self) -> bool:
        return self.side == "buy"


class TradePlan(BaseModel):
    """A proposal. Never an order. The kernel decides whether it becomes one."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = ""
    sleeve: Sleeve
    action: Literal["open", "close", "adjust"]
    instrument: Instrument

    symbol: str = Field(description="equity/crypto ticker, or underlying for options")
    option_legs: list[OptionLeg] = Field(default_factory=list)

    side: Side
    notional_usd: Decimal = Field(gt=0, le=30_000)

    # The model must state a bounded worst case. The kernel recomputes it
    # independently and refuses the plan if the two materially disagree.
    max_loss_usd: Decimal = Field(gt=0)

    stop: Decimal | None = None
    target: Decimal | None = None
    time_exit: datetime | None = None

    # Set by a calendar-triggered strategy, not by the LLM. Routes the plan to
    # the separately budgeted event-trade allowance.
    is_event_trade: bool = False

    # Which catalyst this plan is positioning for, e.g. "ISM Services PMI".
    #
    # AUDIT NOTE: the de-duplication guard used to compare two different things.
    # The scheduler recorded plan.symbol ("SPY"); EventVolStrategy checked
    # event.name ("ADP National Employment"). Those sets never intersect, so the
    # guard never fired and the same catalyst was re-traded on consecutive
    # ticks -- measured at $24,720 of premium, the entire convex sleeve, spent
    # on one print in two minutes, stopped only when the total premium cap
    # finally bound. One key, set by the strategy, read by the scheduler.
    event_key: str | None = None

    thesis: str = Field(min_length=40, max_length=800)
    evidence: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def model_post_init(self, __context) -> None:
        if self.instrument == "option" and not self.option_legs:
            raise ValueError("option plan must carry at least one leg")
        if self.instrument != "option" and self.option_legs:
            raise ValueError("non-option plan must not carry option legs")
        if not self.plan_id:
            payload = self.model_dump(mode="json", exclude={"plan_id"})
            object.__setattr__(self, "plan_id", stable_plan_id("plan", payload))


class Verdict(BaseModel):
    """The kernel's answer. Always has a reason, approved or not."""

    plan_id: str
    approved: bool
    reason: str
    checks_passed: int
    checks_total: int
    failed_invariant: str | None = None

    def __bool__(self) -> bool:
        return self.approved
