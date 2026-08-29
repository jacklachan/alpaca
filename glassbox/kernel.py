"""The risk kernel. Thirteen invariants.

Ordinary Python. No model, no network, no I/O. Every plan is checked against
every invariant and either APPROVED or REFUSED with a reason string.

There is no code path from the thesis layer to the broker that bypasses this.

Each invariant has a named pytest case in tests/test_kernel.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from . import config as C
from .schema import OptionContract, TradePlan, Verdict


@dataclass
class Position:
    symbol: str
    instrument: str                 # equity | crypto | option
    qty: Decimal
    market_value: Decimal
    underlying: str | None = None   # for options
    net_delta_shares: Decimal = Decimal(0)   # signed, share-equivalent
    premium_paid: Decimal = Decimal(0)       # options only


@dataclass
class PortfolioState:
    """Everything the kernel needs. Built by reading the BROKER, never local state."""

    equity: Decimal
    cash: Decimal
    positions: list[Position] = field(default_factory=list)

    core_sleeve_value: Decimal = Decimal(0)
    core_sleeve_cost_basis: Decimal = Decimal(0)

    convex_premium_outstanding: Decimal = Decimal(0)
    convex_premium_today: Decimal = Decimal(0)
    event_premium_today: Decimal = Decimal(0)

    orders_today: int = 0
    orders_today_by_symbol: dict[str, int] = field(default_factory=dict)
    open_client_order_ids: set[str] = field(default_factory=set)

    market_open: bool = True
    now_et: datetime = field(default_factory=lambda: datetime.now(C.ET))
    trading_days_to: dict[date, int] = field(default_factory=dict)

    snapshot_price: dict[str, Decimal] = field(default_factory=dict)
    median_order_notional: Decimal = Decimal(5000)

    kill_switch_tripped: bool = False


def _underlying_of(symbol: str) -> str:
    """Underlying for an OCC contract symbol; the symbol itself otherwise."""
    try:
        from .schema import OptionContract
        return OptionContract.parse(symbol).underlying
    except Exception:
        return symbol


class Refusal(Exception):
    def __init__(self, invariant: str, reason: str):
        self.invariant = invariant
        self.reason = reason
        super().__init__(f"{invariant}: {reason}")


class RiskKernel:
    INVARIANTS = (
        "01_symbol_allowlist",
        "02_bounded_max_loss",
        "03_sleeve_budget",
        "04_daily_burn",
        "05_concentration",
        "06_position_count",
        "07_gross_exposure",
        "08_drawdown_kill_switch",
        "09_market_hours",
        "10_expiry_guard",
        "11_idempotency",
        "12_sanity_band",
        "13_order_frequency",
    )

    def review(self, plan: TradePlan, state: PortfolioState) -> Verdict:
        passed = 0
        for name in self.INVARIANTS:
            try:
                getattr(self, f"_check_{name}")(plan, state)
                passed += 1
            except Refusal as r:
                return Verdict(
                    plan_id=plan.plan_id, approved=False, reason=r.reason,
                    checks_passed=passed, checks_total=len(self.INVARIANTS),
                    failed_invariant=r.invariant,
                )
        return Verdict(
            plan_id=plan.plan_id, approved=True,
            reason=f"all {passed} invariants satisfied",
            checks_passed=passed, checks_total=len(self.INVARIANTS),
        )

    # -- 01 --------------------------------------------------------------------
    def _check_01_symbol_allowlist(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.instrument == "equity":
            if plan.symbol not in C.EQUITY_ALLOWLIST:
                raise Refusal("01_symbol_allowlist", f"{plan.symbol} is not in the equity allowlist")
        elif plan.instrument == "crypto":
            if plan.symbol not in C.CRYPTO_ALLOWLIST:
                raise Refusal("01_symbol_allowlist", f"{plan.symbol} is not in the crypto allowlist")
        else:
            for leg in plan.option_legs:
                u = leg.contract.underlying
                if u not in C.OPTION_UNDERLYING_ALLOWLIST:
                    raise Refusal("01_symbol_allowlist",
                                  f"option underlying {u} is not in the options allowlist")

    # -- 02 --------------------------------------------------------------------
    def _check_02_bounded_max_loss(self, plan: TradePlan, s: PortfolioState) -> None:
        """Worst case must be finite AND independently agree with the plan's claim.

        Options: exactly the premium paid -- but only if every leg is long.
        A short option leg has unbounded (or collateral-bounded) loss and is
        refused unconditionally. This is the single check that makes the whole
        design defensible.

        Equity/crypto: a stop is NOT a bound -- price gaps through stops. The
        estimate is stop distance x qty x a gap multiplier, and it is labelled
        an estimate rather than a guarantee.
        """
        if plan.instrument == "option":
            for leg in plan.option_legs:
                if not leg.is_long:
                    raise Refusal("02_bounded_max_loss",
                                  f"short option leg {leg.symbol} has unbounded maximum loss; "
                                  "this system trades long premium only")
            computed = sum(
                (leg.limit_price or Decimal(0)) * leg.qty * 100 for leg in plan.option_legs
            )
            if computed <= 0:
                raise Refusal("02_bounded_max_loss",
                              "cannot compute premium at risk: leg limit prices missing")
        else:
            if plan.stop is None:
                raise Refusal("02_bounded_max_loss",
                              f"{plan.instrument} plan has no stop; maximum loss is not computable")
            px = s.snapshot_price.get(plan.symbol)
            if px is None or px <= 0:
                raise Refusal("02_bounded_max_loss", f"no snapshot price for {plan.symbol}")
            qty = plan.notional_usd / px
            computed = abs(px - plan.stop) * qty * C.GAP_MULTIPLIER

        claimed = plan.max_loss_usd
        if computed > claimed * Decimal("1.05"):
            raise Refusal("02_bounded_max_loss",
                          f"stated max loss {claimed} understates computed worst case {computed:.2f}")

    # -- 03 --------------------------------------------------------------------
    def _check_03_sleeve_budget(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.sleeve != "convex" or plan.action != "open":
            return
        premium = sum((leg.limit_price or Decimal(0)) * leg.qty * 100 for leg in plan.option_legs)
        after = s.convex_premium_outstanding + premium
        if after > C.CONVEX_TOTAL_PREMIUM_CAP:
            raise Refusal("03_sleeve_budget",
                          f"convex premium outstanding would reach {after:.0f}, "
                          f"cap is {C.CONVEX_TOTAL_PREMIUM_CAP}")

    # -- 04 --------------------------------------------------------------------
    def _check_04_daily_burn(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.sleeve != "convex" or plan.action != "open":
            return
        premium = sum((leg.limit_price or Decimal(0)) * leg.qty * 100 for leg in plan.option_legs)
        if plan.is_event_trade:
            after = s.event_premium_today + premium
            if after > C.EVENT_TRADE_DAILY_CAP:
                raise Refusal("04_daily_burn",
                              f"event-trade premium today would reach {after:.0f}, "
                              f"cap is {C.EVENT_TRADE_DAILY_CAP}")
        else:
            after = s.convex_premium_today + premium
            if after > C.CONVEX_DAILY_BURN_CAP:
                raise Refusal("04_daily_burn",
                              f"convex premium committed today would reach {after:.0f}, "
                              f"cap is {C.CONVEX_DAILY_BURN_CAP}")

    # -- 05 --------------------------------------------------------------------
    def _check_05_concentration(self, plan: TradePlan, s: PortfolioState) -> None:
        """NET share-equivalent delta per underlying, as a fraction of equity.

        Net, not gross. A long strangle is roughly delta-flat and passes; a
        single large directional call does not. Gross would refuse every SPY
        option trade on a $100k account and was the original spec's ambiguity.
        """
        if s.equity <= 0:
            raise Refusal("05_concentration", "equity is zero or negative")

        underlying = plan.symbol if plan.instrument != "option" else plan.option_legs[0].contract.underlying
        existing = sum(
            (p.net_delta_shares for p in s.positions
             if (p.underlying or p.symbol) == underlying), Decimal(0)
        )

        px = s.snapshot_price.get(underlying)
        if px is None or px <= 0:
            raise Refusal("05_concentration", f"no snapshot price for {underlying}")

        if plan.instrument == "option":
            added = Decimal(0)
            for leg in plan.option_legs:
                # Near-ATM approximation; the execution layer refines from the chain.
                d = Decimal("0.5") if leg.contract.right == "C" else Decimal("-0.5")
                added += d * leg.qty * 100 * (1 if leg.is_long else -1)
            net_notional = abs(existing + added) * px
            limit = s.equity * C.OPTION_NET_DELTA_MAX_PCT
            if net_notional > limit:
                raise Refusal("05_concentration",
                              f"net delta exposure to {underlying} would be {net_notional:.0f} "
                              f"({net_notional / s.equity:.0%} of equity), sanity bound is "
                              f"{C.OPTION_NET_DELTA_MAX_PCT:.0%}")
        else:
            added = (plan.notional_usd / px) * (1 if plan.side == "buy" else -1)
            net_notional = abs(existing + added) * px
            limit = s.equity * C.CONCENTRATION_CAPITAL_PCT
            if net_notional > limit:
                raise Refusal("05_concentration",
                              f"capital exposure to {underlying} would be {net_notional:.0f} "
                              f"({net_notional / s.equity:.0%} of equity), limit is "
                              f"{C.CONCENTRATION_CAPITAL_PCT:.0%}")

    # -- 06 --------------------------------------------------------------------
    def _check_06_position_count(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.action != "open":
            return
        counts = {"equity": 0, "crypto": 0, "option": 0}
        for p in s.positions:
            counts[p.instrument] = counts.get(p.instrument, 0) + 1
        if plan.instrument == "equity" and counts["equity"] >= C.MAX_CORE_POSITIONS:
            raise Refusal("06_position_count", f"already holding {counts['equity']} core positions "
                                               f"(max {C.MAX_CORE_POSITIONS})")
        if plan.instrument == "crypto" and counts["crypto"] >= C.MAX_CRYPTO_POSITIONS:
            raise Refusal("06_position_count", f"already holding {counts['crypto']} crypto positions "
                                               f"(max {C.MAX_CRYPTO_POSITIONS})")
        if plan.instrument == "option":
            if counts["option"] + len(plan.option_legs) > C.MAX_OPTION_LEGS:
                raise Refusal("06_position_count",
                              f"{counts['option']} option legs open, plan adds "
                              f"{len(plan.option_legs)} (max {C.MAX_OPTION_LEGS})")

    # -- 07 --------------------------------------------------------------------
    def _check_07_gross_exposure(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.instrument != "equity" or plan.action != "open":
            return
        after = s.core_sleeve_value + plan.notional_usd
        if after > s.equity * C.CORE_GROSS_EXPOSURE_MAX:
            raise Refusal("07_gross_exposure",
                          f"core gross exposure would reach {after:.0f} against equity "
                          f"{s.equity:.0f}; no margin leverage permitted")

    # -- 08 --------------------------------------------------------------------
    def _check_08_drawdown_kill_switch(self, plan: TradePlan, s: PortfolioState) -> None:
        """Per sleeve, not portfolio-wide.

        The convex sleeve is PERMITTED to go to zero -- that is the design, and a
        portfolio switch tight enough to catch a bad core would fire on the
        convex sleeve doing exactly what it was built to do.
        """
        if plan.action == "close":
            return  # never block de-risking
        if s.kill_switch_tripped:
            raise Refusal("08_drawdown_kill_switch",
                          "kill switch is latched; only a human can re-arm it")
        if s.core_sleeve_cost_basis > 0:
            dd = (s.core_sleeve_cost_basis - s.core_sleeve_value) / s.core_sleeve_cost_basis
            if dd >= C.CORE_DRAWDOWN_KILL_PCT:
                raise Refusal("08_drawdown_kill_switch",
                              f"core sleeve drawdown {dd:.1%} at or beyond "
                              f"{C.CORE_DRAWDOWN_KILL_PCT:.0%} limit")
        port_dd = (C.STARTING_EQUITY - s.equity) / C.STARTING_EQUITY
        if port_dd >= C.PORTFOLIO_DRAWDOWN_KILL_PCT:
            raise Refusal("08_drawdown_kill_switch",
                          f"portfolio drawdown {port_dd:.1%} at or beyond "
                          f"{C.PORTFOLIO_DRAWDOWN_KILL_PCT:.0%} backstop")

    # -- 09 --------------------------------------------------------------------
    def _check_09_market_hours(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.instrument == "crypto":
            return  # 24/7
        if not s.market_open:
            raise Refusal("09_market_hours",
                          f"{plan.instrument} order refused: market is closed "
                          "(options have no extended-hours session)")

    # -- 10 --------------------------------------------------------------------
    def _check_10_expiry_guard(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.instrument != "option":
            return
        for leg in plan.option_legs:
            exp = leg.contract.expiry
            dte = s.trading_days_to.get(exp)
            if dte is None:
                raise Refusal("10_expiry_guard", f"no trading-day count for expiry {exp}")
            if plan.action == "open":
                if dte < C.OPTION_MIN_DTE:
                    raise Refusal("10_expiry_guard",
                                  f"{leg.symbol} expires in {dte} trading days, "
                                  f"minimum is {C.OPTION_MIN_DTE}")
                if dte > C.OPTION_MAX_DTE:
                    raise Refusal("10_expiry_guard",
                                  f"{leg.symbol} expires in {dte} trading days, "
                                  f"maximum is {C.OPTION_MAX_DTE}")

    # -- 11 --------------------------------------------------------------------
    def _check_11_idempotency(self, plan: TradePlan, s: PortfolioState) -> None:
        from .ids import client_order_id
        n = len(plan.option_legs) or 1
        for i in range(n):
            coid = client_order_id(
                plan.plan_id, i, event=plan.is_event_trade)
            if coid in s.open_client_order_ids:
                raise Refusal("11_idempotency",
                              f"client_order_id {coid[:16]} already exists at the broker")

    # -- 12 --------------------------------------------------------------------
    def _check_12_sanity_band(self, plan: TradePlan, s: PortfolioState) -> None:
        if plan.notional_usd > s.median_order_notional * C.NOTIONAL_SANITY_MULTIPLE:
            raise Refusal("12_sanity_band",
                          f"notional {plan.notional_usd:.0f} exceeds "
                          f"{C.NOTIONAL_SANITY_MULTIPLE}x median order size "
                          f"{s.median_order_notional:.0f}")
        if plan.instrument != "option":
            px = s.snapshot_price.get(plan.symbol)
            if px and plan.stop is not None:
                if abs(plan.stop - px) / px > Decimal("0.5"):
                    raise Refusal("12_sanity_band",
                                  f"stop {plan.stop} is implausibly far from snapshot {px}")
        else:
            for leg in plan.option_legs:
                u = leg.contract.underlying
                px = s.snapshot_price.get(u)
                if px and abs(leg.contract.strike - px) / px > Decimal("0.15"):
                    raise Refusal("12_sanity_band",
                                  f"{leg.symbol} strike {leg.contract.strike} is far from "
                                  f"{u} spot {px}")

    # -- 13 --------------------------------------------------------------------
    def _check_13_order_frequency(self, plan: TradePlan, s: PortfolioState) -> None:
        """Runaway-loop breaker.

        Idempotency stops the SAME plan being sent twice. Nothing else stops the
        agent generating hundreds of DISTINCT plans, which is the classic way an
        unattended agent destroys an account overnight.
        """
        if s.orders_today >= C.MAX_ORDERS_PER_DAY:
            raise Refusal("13_order_frequency",
                          f"{s.orders_today} orders already placed today "
                          f"(max {C.MAX_ORDERS_PER_DAY}); halting and alerting")
        # AUDIT NOTE: this compared plan.symbol against a map keyed by the
        # symbol the ORDER was placed in. For an option plan those are
        # different things -- plan.symbol is the underlying ("SPY"), while the
        # order key is the OCC contract ("SPY260904C00783000"). The per-symbol
        # cap was therefore unreachable for every option trade: 100 orders in
        # the exact same contract still passed an 8-order limit. Count orders
        # in the plan's own contracts, and for options also count them against
        # the underlying, so a runaway loop is caught either way.
        sym = plan.symbol
        counted = {sym: s.orders_today_by_symbol.get(sym, 0)}
        for leg in (plan.option_legs or []):
            counted[leg.symbol] = s.orders_today_by_symbol.get(leg.symbol, 0)
        if plan.option_legs:
            underlying_total = sum(
                n for k, n in s.orders_today_by_symbol.items()
                if _underlying_of(k) == sym)
            counted[f"{sym} (all contracts)"] = underlying_total

        for label, n in counted.items():
            if n >= C.MAX_ORDERS_PER_SYMBOL_PER_DAY:
                raise Refusal("13_order_frequency",
                              f"{n} orders already placed in {label} today "
                              f"(max {C.MAX_ORDERS_PER_SYMBOL_PER_DAY})")
