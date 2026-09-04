"""Sync generated counts in the judge-facing write-ups to the real suite.

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
# Every document that quotes a count, not just the one-pager. The long
# write-up drifted to a stale number because only the short one was synced,
# and a judge who opens both sees two answers to the same question.
WRITEUPS = (
    ROOT / "docs" / "WRITEUP.md",
    ROOT / "docs" / "WRITEUP-FULL.md",
)


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
    for writeup in WRITEUPS:
        name = writeup.relative_to(ROOT).as_posix()
        text = writeup.read_text(encoding="utf-8")
        updated = re.sub(r"\b[\d,]+ automated tests", f"{count} automated tests", text)
        if updated == text:
            print(f"{name} already states {count} automated tests")
            continue
        writeup.write_text(updated, encoding="utf-8")
        print(f"{name} updated to {count} automated tests")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
