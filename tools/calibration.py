"""What the risk model said before it traded, against what actually happened.

    python tools/calibration.py

Every other claim in this repository is about what the agent is *prevented*
from doing. This one asks whether the numbers the system computed before it
traded turned out to be true.

Before each order, the surface gate computes how much of the premium is certain
to decay before the account is valued, and writes that figure into the
hash-chained journal as evidence for the decision. Those entries are timestamped
and chained, so they provably predate the outcome. They cannot be fitted after
the fact without breaking the chain, which `tools/verify_chain.py` reports.

Two things this tool is careful about, both learned the hard way:

1. **It freezes at the measurement instant.** An earlier version compared the
   forecast against whatever was open when you happened to run it. That made
   the result drift -- it read -1.8% of premium while the book was a balanced
   strangle and -181.8% an hour later, once one leg was closed and only a
   directional winner remained. A published number that changes with the clock
   is not evidence. Everything below is read at 2026-09-03 16:00 ET and does
   not move.

2. **It does not claim the decay model was validated.** A mark change contains
   decay *and* direction, and marks alone cannot separate them. The forecast
   was about decay only. So this prints both numbers, says what the difference
   does and does not mean, and leaves the conclusion to the reader.

No credentials are needed for the journal half. Alpaca's official close needs
them; without them that section is skipped rather than guessed.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from glassbox import config as C  # noqa: E402
from glassbox.macro import MEASUREMENT_ET  # noqa: E402

G, R, Y, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"

_DECAY = re.compile(r"decay to measurement ([0-9.]+) of premium over ([0-9.]+)d")
_PREMIUM = re.compile(r"premium_at_risk=([0-9.]+)")

MEASUREMENT_UTC = MEASUREMENT_ET.astimezone(timezone.utc)


def _ts(record: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(record["ts"]).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def read_journal(path: Path) -> tuple[list[dict], dict | None]:
    """Pre-trade forecasts, and the account state frozen at measurement.

    One pass, because the journal is large and this runs on a laptop.
    """
    forecasts: list[dict] = []
    frozen: dict | None = None
    if not path.exists():
        return forecasts, frozen

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "decay to measurement" in line:
                record = json.loads(line)
                blob = json.dumps(record.get("payload", {}))
                decay = _DECAY.search(blob)
                if decay:
                    premium = _PREMIUM.search(blob)
                    forecasts.append(
                        {
                            "seq": record["seq"],
                            "ts": record["ts"][:19],
                            "pct": Decimal(decay.group(1)),
                            "days": Decimal(decay.group(2)),
                            "premium": Decimal(premium.group(1)) if premium else None,
                        }
                    )
                continue

            # The last reconciliation at or before the measurement instant is
            # the account as the agent saw it when it was scored.
            if '"RECONCILED"' not in line or "equity" not in line:
                continue
            record = json.loads(line)
            when = _ts(record)
            if when is None or when > MEASUREMENT_UTC:
                continue
            payload = record.get("payload", {})
            if "equity" not in payload:
                continue
            frozen = {
                "seq": record["seq"],
                "ts": record["ts"][:19],
                "equity": Decimal(str(payload["equity"])),
                "premium_out": (
                    Decimal(str(payload["convex_premium_outstanding"]))
                    if payload.get("convex_premium_outstanding") is not None
                    else None
                ),
                "positions": payload.get("positions"),
            }
    return forecasts, frozen


def official_close() -> Decimal | None:
    """Alpaca's own closing equity for the measurement day, or None."""
    try:
        from glassbox.broker import Broker

        history = Broker(journal=None).portfolio_history(period="7D", timeframe="1D")
        target = MEASUREMENT_ET.date()
        for stamp, equity in zip(history.timestamp, history.equity):
            if datetime.fromtimestamp(stamp, timezone.utc).date() == target:
                return Decimal(str(equity))
    except Exception:
        return None
    return None


def main() -> int:
    print(f"\n{B}What the risk model said, before it traded{X}")
    print(f"{D}Forecasts come from the hash-chained journal, written before each")
    print("order. The outcome is frozen at the measurement instant, so this")
    print(f"result does not change depending on when you run it.{X}\n")

    forecasts, frozen = read_journal(Path(C.JOURNAL_PATH))
    if not forecasts:
        print(f"  {Y}no decay forecast recorded yet{X}\n")
        return 0

    print(f"  {B}Forecast, at entry{X}")
    predicted = Decimal(0)
    committed = Decimal(0)
    for f in forecasts:
        line = f"  seq {f['seq']:<7} {f['ts']}  {float(f['pct']):.1%} of premium over {f['days']}d"
        if f["premium"] is not None:
            predicted += f["premium"] * f["pct"]
            committed += f["premium"]
            line += f"   (${f['premium']:,.0f} committed)"
        print(line)

    if committed <= 0:
        print(f"\n  {Y}forecasts recorded, but no premium figure alongside them{X}\n")
        return 0

    print(
        f"\n  predicted decay  ${predicted:,.0f}"
        f"  ({predicted / committed:.1%} of ${committed:,.0f} committed)"
    )

    stamp = MEASUREMENT_ET.strftime("%Y-%m-%d %H:%M ET")
    if frozen is None:
        print(f"\n  {Y}no reconciliation recorded at or before {stamp}{X}\n")
        return 0

    print(f"\n  {B}Account at the measurement instant{X}  {D}({stamp}){X}")
    # No lookahead: this is the last reading at or before the instant, so
    # say how stale it is rather than implying it was taken exactly then.
    early = (MEASUREMENT_UTC - _ts({"ts": frozen["ts"]})).total_seconds()
    print(f"  journal seq       {frozen['seq']}  at {frozen['ts']}Z"
          f"  ({early:.0f}s before the instant, no lookahead)")
    print(f"  equity            ${frozen['equity']:,.2f}")
    if frozen["premium_out"] is not None:
        print(f"  premium still open ${frozen['premium_out']:,.0f}")
        closed = committed - frozen["premium_out"]
        print(f"  premium closed before measurement ${closed:,.0f}")

    print(f"\n{D}  What this does and does not show.")
    print("  The forecast was about DECAY -- the premium certain to be lost")
    print("  simply by holding to the measurement. The account's change also")
    print("  contains DIRECTION, and a mark cannot be split into the two after")
    print("  the fact. So the difference below is not model error; most of it")
    print("  is the trade being right or wrong about where QQQ went.")
    print("  Most of the forecast premium was also closed before measurement,")
    print("  so the forecast and the surviving book are not the same position.")
    print(f"  Read this as the forecast on the record, not as a scorecard.{X}")

    official = official_close()
    if official is None:
        print(f"\n  {D}Alpaca's official close needs credentials; skipped.{X}\n")
        return 0

    gap = frozen["equity"] - official
    print(f"\n  {B}Two numbers for the same instant{X}")
    print(f"  our mark, indicative feed   ${frozen['equity']:,.2f}")
    print(f"  Alpaca's official close     ${official:,.2f}")
    colour = G if abs(gap) < Decimal("1000") else Y
    print(f"  {colour}gap                         ${gap:+,.2f}  ({gap / official:+.2%} of the account){X}")

    print(f"\n{D}  This is the whole reason the measurement-aware exit exists. We")
    print("  price options off Alpaca's INDICATIVE feed, a derived estimate.")
    print("  At the instant that decides the result, our own honest reading of")
    print("  the book and the broker's official close disagreed by the amount")
    print("  above. Neither is a lie; an option mark is an opinion until it is")
    print("  cash. That is why the agent would rather hold a number it can")
    print(f"  defend than one that merely looks better.{X}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
