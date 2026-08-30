"""Startup gate. Runs as systemd ExecStartPre, and by hand before cutover.

    python -m glassbox.preflight

Exit 0 = safe to start. Exit 1 = do not start, with the reason on stdout.

This exists because the dangerous configuration errors in this system are the
quiet ones. A wrong API key fails loudly on the first call. A .env whose inline
comments survive systemd's parser does not fail at all -- it starts, trades, and
silently skips the scored-account guards. Preflight turns that class of bug back
into a loud one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    envfile = root / ".env"
    load_dotenv(envfile)

    from glassbox import env

    problems = env.preflight(envfile, strict=False)

    if not problems:
        mode = env.get("ALPACA_ENV", "dev")
        print(
            f"preflight OK: paper=true, env={mode}, .env parses identically "
            f"under systemd and python-dotenv"
        )
        if mode == "scored":
            print(
                "NOTE: pointed at the SCORED account. Startup will additionally "
                "assert equity == 100000 and no open positions."
            )
        return 0

    print("PREFLIGHT FAILED -- refusing to start:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
