"""See the safety layer work, with no credentials and no network.

    python tools/demo.py

Everything below runs the REAL shipped code -- the same RiskKernel, the same
PositionManager, the same Journal the scored agent runs. Nothing is mocked
except the market state handed to them, because the point is not that a demo
passes but that the code you are about to read behaves as the README claims.

Why this exists: every other entry point needs Alpaca credentials, so a reader
could inspect the kernel but never watch it refuse anything. A claim you cannot
run is a claim.

Four things, in about ten seconds:

  1. The kernel refusing hostile plans, each with the invariant that caught it.
  2. The measurement-approach rule taking cash over a mark it cannot defend.
  3. A hash-chained journal verifying.
  4. The same journal failing after one byte is changed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glassbox.journal import Journal  # noqa: E402
from glassbox.kernel import PortfolioState, Position, RiskKernel  # noqa: E402
from glassbox.macro import MEASUREMENT_ET  # noqa: E402
from glassbox.manage import KillSwitch, PositionManager  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from practice import hostile_plans  # noqa: E402

BAR = "=" * 74
G, R, Y, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def head(n: int, title: str) -> None:
    print(f"\n{BAR}\n{B}{n}. {title}{X}\n{BAR}")


class RecordingBroker:
    """Records closes instead of sending them. No network, no credentials."""

    def __init__(self) -> None:
        self.closed: list[str] = []

    def close_position(self, symbol: str) -> None:
        self.closed.append(symbol)


def demo_state(**kw: Any) -> PortfolioState:
    base: dict[str, Any] = dict(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        core_sleeve_value=Decimal(0),
        core_sleeve_cost_basis=Decimal(0),
        snapshot_price={"SPY": Decimal("769.28"), "QQQ": Decimal("716.91")},
        trading_days_to={
            date(2026, 9, 4): 3,
            date(2026, 9, 8): 4,
            date(2026, 9, 11): 7,
        },
        market_open=True,
        median_order_notional=Decimal("6000"),
    )
    base.update(kw)
    return PortfolioState(**base)


def main() -> int:
    print(f"\n{B}Glassbox - the safety layer, with no credentials{X}")
    print(f"{D}Real kernel, real journal, real position manager. Synthetic market only.{X}")

    kernel = RiskKernel()
    state = demo_state()

    # -- 1 --------------------------------------------------------------------
    head(1, "The kernel refusing hostile plans")
    print(f"{D}Each of these validates against the schema and reaches the kernel.")
    print("The schema is permissive on purpose: a plan that dies in validation")
    print(f"is refused invisibly, with no reason string and no journal entry.{X}\n")

    all_refused = True
    for label, plan in hostile_plans():
        verdict = kernel.review(plan, state)
        mark = f"{G}REFUSED{X}" if not verdict.approved else f"{R}APPROVED{X}"
        print(f"  {mark}  {label}")
        print(f"           {D}{verdict.failed_invariant}{X}")
        print(f"           {verdict.reason}\n")
        all_refused &= not verdict.approved

    print(
        f"  {G}All {len(hostile_plans())} refused.{X}"
        if all_refused
        else f"  {R}A HOSTILE PLAN PASSED.{X}"
    )

    # -- 2 --------------------------------------------------------------------
    head(2, "Taking cash over a mark it cannot defend")
    print(f"{D}The account is valued at a known instant, off the indicative feed.")
    print("A contract quoting 30% wide at that moment is not evidence of anything,")
    print(f"and cash has no marking ambiguity. Inside the window, it flattens.{X}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        journal = Journal(tmpdir / "j.jsonl")
        broker = RecordingBroker()
        manager = PositionManager(
            broker,
            journal,
            KillSwitch(tmpdir / "kill.json", journal=journal),
            targets_path=tmpdir / "targets.json",
            exit_state_path=tmpdir / "exits.json",
        )
        symbol = "SPY260911C00780000"
        held = Position(
            symbol=symbol,
            instrument="option",
            qty=Decimal(5),
            market_value=Decimal("2500"),
            underlying="SPY",
            premium_paid=Decimal("2500"),
        )

        for label, spread, minutes in (
            ("tight quote, well before measurement", "0.03", 240),
            ("tight quote, inside the window", "0.03", 20),
            ("30% wide, inside the window", "0.30", 20),
            ("no two-sided quote at all", None, 20),
        ):
            broker.closed.clear()
            s = demo_state(
                positions=[held],
                now_et=MEASUREMENT_ET - timedelta(minutes=minutes),
                option_quote_spread={} if spread is None else {symbol: Decimal(spread)},
            )
            exits = manager.tick(s)
            manager._exits_sent.clear()  # so each scenario is judged independently
            if exits:
                print(f"  {Y}FLATTEN{X}  {label}")
                print(f"           {exits[0].reason}\n")
            else:
                print(f"  {G}HOLD   {X}  {label}")
                print(f"           {D}the mark is defensible; cash would be worse{X}\n")

        # -- 3 ----------------------------------------------------------------
        head(3, "The journal verifies")
        ok, why = journal.verify()
        print(f"  {G if ok else R}{'PASS' if ok else 'FAIL'}{X}  {why}")

        # -- 4 ----------------------------------------------------------------
        head(4, "The same journal, after one byte is changed")
        path = journal.path
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            print(f"  {Y}nothing recorded to tamper with{X}")
            return 0
        record = json.loads(lines[0])
        payload = record.get("payload")
        if isinstance(payload, dict) and "reason" in payload:
            payload["reason"] = "looked fine to me"
        else:
            record["actor"] = "someone.else"
        lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok_after, why_after = Journal(path).verify()
        print(
            f"  {G if not ok_after else R}{'FAIL (correct)' if not ok_after else 'PASS'}{X}"
            f"  {why_after}"
        )
        print(f"\n  {D}Honest scope: this detects edits to the recorded history. It does")
        print("  not prove the history was never regenerated -- we control every input")
        print("  to the hash. Broker order ids and venue timestamps are what a third")
        print(f"  party reconciles against. We claim reconcilable, not tamper-proof.{X}")

    print(f"\n{BAR}")
    print(f"{D}Next, still without credentials:")
    print("  python tools/verify_chain.py        the live journal's chain")
    print(f"  python tools/verify_submission.py   every published claim, re-checked{X}")
    print(f"{BAR}\n")
    return 0 if all_refused else 1


if __name__ == "__main__":
    raise SystemExit(main())
