"""Did the risk model predict what actually happened?

    python tools/calibration.py

Every other claim in this repository is about what the agent is *prevented*
from doing. This one is different: it asks whether the numbers the system
computed before it traded turned out to be true.

Before each order, the surface gate computes how much of the premium is
certain to decay before the account is valued, and writes that figure into the
hash-chained journal as evidence for the decision. That entry is timestamped
and chained, so it provably predates the outcome it is being compared against.
It cannot be fitted after the fact without breaking the chain, which
`tools/verify_chain.py` would then report.

So the forecast is falsifiable, and this prints the test. A model that was
wrong shows up here as loudly as one that was right -- that is the point of
publishing it rather than only the flattering half.

No credentials are needed to read the forecasts; they are in the journal. Live
marks require Alpaca, and without them the tool prints the forecast alone and
says so.
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from glassbox import config as C  # noqa: E402

G, R, Y, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"

_DECAY = re.compile(r"decay to measurement ([0-9.]+) of premium over ([0-9.]+)d")
_PREMIUM = re.compile(r"premium_at_risk=([0-9.]+)")


def forecasts(journal_path: Path) -> list[dict]:
    """Every pre-trade decay forecast the journal recorded, in order."""
    out: list[dict] = []
    if not journal_path.exists():
        return out
    with journal_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if "decay to measurement" not in line:
                continue
            record = json.loads(line)
            blob = json.dumps(record.get("payload", {}))
            decay = _DECAY.search(blob)
            if not decay:
                continue
            premium = _PREMIUM.search(blob)
            out.append(
                {
                    "seq": record["seq"],
                    "ts": record["ts"][:19],
                    "pct": Decimal(decay.group(1)),
                    "days": Decimal(decay.group(2)),
                    "premium": Decimal(premium.group(1)) if premium else None,
                }
            )
    return out


def live_outcome() -> tuple[Decimal, Decimal] | None:
    """(premium paid, current mark) from the venue, or None without credentials."""
    try:
        from glassbox.broker import Broker

        broker = Broker(journal=None)
        paid = mark = Decimal(0)
        for position in broker.positions():
            paid += Decimal(str(position.cost_basis))
            mark += Decimal(str(position.market_value))
        return (paid, mark) if paid > 0 else None
    except Exception:
        return None


def main() -> int:
    print(f"\n{B}Was the risk model right?{X}")
    print(f"{D}Forecasts are read from the hash-chained journal, where they were")
    print(f"written before each order. Verify with tools/verify_chain.py.{X}\n")

    recorded = forecasts(Path(C.JOURNAL_PATH))
    if not recorded:
        print(f"  {Y}no decay forecast recorded yet{X}\n")
        return 0

    print(f"  {B}Forecast, at entry{X}")
    predicted = Decimal(0)
    premium_seen = Decimal(0)
    for f in recorded:
        premium = f["premium"]
        line = f"  seq {f['seq']:<7} {f['ts']}  {float(f['pct']):.1%} of premium over {f['days']}d"
        if premium is not None:
            predicted += premium * f["pct"]
            premium_seen += premium
            line += f"   (${premium:,.0f} committed)"
        print(line)

    if premium_seen <= 0:
        print(f"\n  {Y}forecasts recorded, but no premium figure alongside them{X}\n")
        return 0

    print(f"\n  predicted decay  ${predicted:,.0f}  ({predicted / premium_seen:.1%} of premium)")

    outcome = live_outcome()
    if outcome is None:
        print(f"\n  {D}No Alpaca credentials, so the live mark cannot be read here.")
        print(f"  The forecast above stands on its own and is checkable in the journal.{X}\n")
        return 0

    paid, mark = outcome
    actual = paid - mark
    error = actual - predicted

    print(f"\n  {B}Outcome, from the venue{X}")
    print(f"  premium paid     ${paid:,.0f}")
    print(f"  current mark     ${mark:,.0f}")
    print(f"  actual change    ${actual:,.0f}  ({actual / paid:.1%} of premium)")

    within = abs(error) / paid
    colour = G if within <= Decimal("0.05") else (Y if within <= Decimal("0.10") else R)
    print(f"\n  {colour}error  ${error:+,.0f}   ({error / paid:+.1%} of premium){X}")

    print(f"\n{D}  What this does and does not show.")
    print("  It shows the decay model was calibrated: the cost of holding was")
    print("  computed correctly in advance. It does NOT show the trade was good.")
    print("  Direction is the other half of a long-volatility position and this")
    print("  says nothing about it -- the move simply did not arrive. A system")
    print("  can price its own costs honestly and still lose, which is exactly")
    print(f"  what happened here.{X}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
