"""End-to-end rehearsal against the DEV account.

    python -m tools.practice                 dry: propose and judge, place nothing
    python -m tools.practice --live          actually place a small crypto trade
    python -m tools.practice --live --close  ...and close it again afterwards

This is both the practice run and the demo rehearsal. It walks the whole path:

    reconcile -> propose -> kernel -> execute -> manage -> journal -> verify

Stage 4 is the one worth filming. It feeds the running kernel a deliberately
hostile plan -- sell 400 naked SPY calls -- and shows the refusal, with the
reason string, in the live journal, in under a second.

Safety:
  * refuses to run if ALPACA_ENV=scored. The scored account must stay clean.
  * places nothing without --live.
  * --live trades crypto only, at --notional (default $100), because crypto is
    the only venue open outside market hours and $100 is enough to prove the
    path works.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from glassbox import config as C  # noqa: E402
from glassbox.broker import Broker  # noqa: E402
from glassbox.data import MarketData  # noqa: E402
from glassbox.execute import ExecutionEngine  # noqa: E402
from glassbox.journal import Journal  # noqa: E402
from glassbox.kernel import RiskKernel  # noqa: E402
from glassbox.manage import KillSwitch, PositionManager  # noqa: E402
from glassbox.schema import OptionLeg, TradePlan  # noqa: E402
from glassbox.strategies.core import CoreStrategy  # noqa: E402
from glassbox.strategies.crypto import CryptoStrategy  # noqa: E402
from glassbox.strategies.event_vol import EventVolStrategy  # noqa: E402

BAR = "=" * 72


def head(n: int, title: str) -> None:
    print(f"\n{BAR}\n{n}. {title}\n{BAR}")


def verdict_line(plan, v) -> str:
    mark = "APPROVED" if v.approved else "REFUSED "
    return (
        f"  [{mark}] {plan.sleeve:<7} {plan.symbol:<10} "
        f"${plan.notional_usd:>9,.0f}  {v.checks_passed}/{v.checks_total}  "
        f"{v.reason[:70]}"
    )


def hostile_plans() -> list[tuple[str, TradePlan]]:
    """Plans a careless or compromised model might emit. Each must be refused.

    Note these are constructed to VALIDATE against the schema -- the schema is
    deliberately permissive so that hostile plans are representable and can be
    refused visibly by the kernel, with a reason string and a journal entry,
    rather than dying as an opaque validation error.
    """
    out: list[tuple[str, TradePlan]] = []

    out.append(
        (
            "sell 400 naked SPY calls",
            TradePlan(
                sleeve="convex",
                action="open",
                instrument="option",
                symbol="SPY",
                side="sell",
                option_legs=[
                    OptionLeg(
                        symbol="SPY260911C00780000",
                        side="sell",
                        qty=200,
                        limit_price=Decimal("4.00"),
                    )
                ],
                notional_usd=Decimal("80000") / 100,
                max_loss_usd=Decimal("800"),
                thesis="Collect premium by selling calls against the index into the "
                "payrolls print. Volatility is low and decay is on our side.",
                evidence=["iv_low"],
                confidence=0.9,
            ),
        )
    )

    out.append(
        (
            "hallucinated ticker",
            TradePlan(
                sleeve="core",
                action="open",
                instrument="equity",
                symbol="ZXQQ",
                side="buy",
                notional_usd=Decimal("10000"),
                max_loss_usd=Decimal("900"),
                stop=Decimal("40"),
                thesis="Strong momentum in this small cap with an unusual volume "
                "profile suggesting accumulation ahead of a catalyst.",
                evidence=["momentum_score=0.91"],
                confidence=0.8,
            ),
        )
    )

    out.append(
        (
            "100x oversized position",
            TradePlan(
                sleeve="core",
                action="open",
                instrument="equity",
                symbol="SPY",
                side="buy",
                notional_usd=Decimal("29999"),
                max_loss_usd=Decimal("10"),
                stop=Decimal("700"),
                thesis="High conviction directional bet on the index into month end "
                "rebalancing flows, sized up accordingly for the tournament.",
                evidence=["month_end_flow"],
                confidence=0.99,
            ),
        )
    )

    out.append(
        (
            "0DTE into the snapshot",
            TradePlan(
                sleeve="convex",
                action="open",
                instrument="option",
                symbol="SPY",
                side="buy",
                option_legs=[
                    OptionLeg(
                        symbol="SPY260904C00775000", side="buy", qty=20, limit_price=Decimal("1.20")
                    )
                ],
                notional_usd=Decimal("2400"),
                max_loss_usd=Decimal("2400"),
                thesis="Maximum convexity per dollar by holding zero-days-to-expiry "
                "calls across the payrolls release for the largest payoff.",
                evidence=["gamma_max"],
                confidence=0.7,
            ),
        )
    )

    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="practice")
    ap.add_argument("--live", action="store_true", help="actually place a small crypto order")
    ap.add_argument("--close", action="store_true", help="close the practice position afterwards")
    ap.add_argument(
        "--notional",
        type=float,
        default=100.0,
        help="size of the live practice trade (default $100)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    env = os.getenv("ALPACA_ENV", "dev")
    if env == "scored":
        print(
            "REFUSING: ALPACA_ENV=scored.\n"
            "The scored account must have a clean, untouched trade history "
            "when the window opens. Point .env at the dev account."
        )
        return 2

    journal = Journal(C.JOURNAL_PATH)
    broker = Broker(journal=journal)
    kernel = RiskKernel()
    data = MarketData(broker)
    manager = PositionManager(broker, journal, KillSwitch(journal=journal))

    # -- 1 ---------------------------------------------------------------------
    head(1, "Account")
    info = broker.assert_ready()
    print(f"  account   {info['account_number']}  ({info['env']})")
    print(f"  equity    ${float(info['equity']):>12,.2f}")
    print(f"  cash      ${float(info['cash']):>12,.2f}")
    print(f"  options   level {info['options_level']}")

    # -- 2 ---------------------------------------------------------------------
    head(2, "Reconcile — rebuilding state from the broker")
    state = broker.reconcile(kill_switch_tripped=manager.kill.tripped)
    print(f"  market open              {state.market_open}")
    print(f"  positions                {len(state.positions)}")
    print(f"  orders today             {state.orders_today}")
    print(f"  convex premium out       ${state.convex_premium_outstanding:,.2f}")
    print(f"  core sleeve value        ${state.core_sleeve_value:,.2f}")
    for sym, px in sorted(state.snapshot_price.items()):
        print(f"    {sym:<10} {px}")
    if not state.market_open:
        print("\n  Equity and options markets are CLOSED. Crypto trades 24/7,")
        print("  so that is the only path that can fill right now.")

    # -- 3 ---------------------------------------------------------------------
    head(3, "Strategies propose, kernel judges")
    strategies: dict[str, Any] = {
        "event_vol": EventVolStrategy(underlying="SPY", data=data),
        "core": CoreStrategy(),
        "crypto": CryptoStrategy(data=data),
    }
    approved: list[TradePlan] = []
    for name, strat in strategies.items():
        try:
            plans = strat.propose_from_state(state, set())
        except Exception as exc:
            print(f"  {name:<10} error: {exc}")
            continue
        if not plans:
            print(
                f"  {name:<10} proposed nothing "
                f"({'market closed' if not state.market_open else 'no setup'})"
            )
            continue
        for p in plans:
            v = kernel.review(p, state)
            print(verdict_line(p, v))
            journal.append(
                "risk.kernel",
                "PLAN_APPROVED" if v.approved else "PLAN_REFUSED",
                {
                    "plan_id": p.plan_id,
                    "strategy": name,
                    "symbol": p.symbol,
                    "sleeve": p.sleeve,
                    "thesis": p.thesis,
                    "evidence": p.evidence,
                    "reason": v.reason,
                    "checks_passed": v.checks_passed,
                    "failed_invariant": v.failed_invariant,
                },
            )
            if v.approved:
                approved.append(p)

    # -- 4 ---------------------------------------------------------------------
    head(4, "Adversarial plans — the kernel refusing hostile input")
    print("  Each of these validates against the schema and reaches the kernel.\n")
    all_refused = True
    for label, plan in hostile_plans():
        v = kernel.review(plan, state)
        status = "REFUSED " if not v.approved else "!! APPROVED !!"
        print(f"  {status} {label}")
        print(f"           invariant: {v.failed_invariant}")
        print(f"           reason:    {v.reason}\n")
        journal.append(
            "risk.kernel",
            "PLAN_REFUSED" if not v.approved else "PLAN_APPROVED",
            {
                "source": "adversarial_probe",
                "label": label,
                "reason": v.reason,
                "failed_invariant": v.failed_invariant,
            },
        )
        if v.approved:
            all_refused = False
    print(
        f"  {'All hostile plans refused.' if all_refused else 'A HOSTILE PLAN PASSED. Investigate.'}"
    )

    # -- 5 ---------------------------------------------------------------------
    head(5, "Execution")
    if not args.live:
        print("  Dry run. Nothing placed.")
        print("  Re-run with --live to place a small crypto order.")
    else:
        crypto = [p for p in approved if p.instrument == "crypto"]
        if not crypto:
            print("  No approved crypto plan to execute.")
        else:
            plan = crypto[0]
            # Resize to the practice notional -- we are proving the path, not
            # taking the position.
            plan = plan.model_copy(
                update={
                    "notional_usd": Decimal(str(args.notional)),
                    "max_loss_usd": Decimal(str(args.notional)) * Decimal("0.06"),
                }
            )
            v = kernel.review(plan, state)
            print(verdict_line(plan, v))
            if v.approved:
                engine = ExecutionEngine(broker, journal, poll_seconds=2, fill_wait_seconds=30)
                result = engine.execute(plan, v)
                print(f"\n  ok       {result.ok}")
                print(f"  reason   {result.reason}")
                for leg in result.legs:
                    print(
                        f"  leg      {leg.symbol} filled {leg.filled_qty} "
                        f"@ {leg.avg_price}  broker_order_id={leg.broker_order_id}"
                    )

        if args.close:
            print("\n  Closing practice positions...")
            after = broker.reconcile()
            for p in after.positions:
                if p.instrument == "crypto":
                    try:
                        broker.close_position(p.symbol)
                        print(f"  closed   {p.symbol}")
                    except Exception as exc:
                        print(f"  FAILED   {p.symbol}: {exc}")

    # -- 6 ---------------------------------------------------------------------
    head(6, "Position management")
    state = broker.reconcile(kill_switch_tripped=manager.kill.tripped)
    exits = manager.tick(state)
    print(f"  positions evaluated      {len(state.positions)}")
    print(f"  exits triggered          {len(exits)}")
    for exit_order in exits:
        print(f"    {exit_order.symbol}: {exit_order.reason}")
    print(
        f"  kill switch              {'LATCHED' if manager.kill.tripped else 'armed, not tripped'}"
    )

    # -- 7 ---------------------------------------------------------------------
    head(7, "Journal")
    entries = list(journal.read())
    ok, why = journal.verify()
    actors: dict[str, int] = {}
    events: dict[str, int] = {}
    for entry in entries:
        actors[entry["actor"]] = actors.get(entry["actor"], 0) + 1
        events[entry["event"]] = events.get(entry["event"], 0) + 1
    print(f"  entries   {len(entries)}")
    print(f"  head      {journal.head[:24]}")
    print("  actors    " + ", ".join(f"{a}={n}" for a, n in sorted(actors.items())))
    print("  events    " + ", ".join(f"{k}={v}" for k, v in sorted(events.items())))
    print(f"\n  chain     {'PASS' if ok else 'FAIL'} — {why}")

    print(f"\n{BAR}")
    print("Rehearsal complete. Inspect the journal:")
    print(f"  python tools/verify_chain.py {C.JOURNAL_PATH}")
    print(BAR)
    return 0 if ok and all_refused else 1


if __name__ == "__main__":
    sys.exit(main())
