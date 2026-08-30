"""Sync generated counts in docs/WRITEUP.md to the real suite.

The write-up quotes a test count to judges. tests/test_claims.py fails when
that number drifts, which is the right guard -- but the fix should be one
command, not a hand edit, or the temptation is to weaken the guard instead.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITEUP = ROOT / "docs" / "WRITEUP.md"


def suite_size() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    found = re.search(r"(\d+) tests collected", result.stdout)
    if not found:
        raise SystemExit(f"could not count the suite:\n{result.stdout[-800:]}")
    return int(found.group(1))


def main() -> int:
    count = suite_size()
    text = WRITEUP.read_text(encoding="utf-8")
    updated = re.sub(r"\b[\d,]+ automated tests", f"{count} automated tests", text)
    if updated == text:
        print(f"docs/WRITEUP.md already states {count} automated tests")
        return 0
    WRITEUP.write_text(updated, encoding="utf-8")
    print(f"docs/WRITEUP.md updated to {count} automated tests")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
