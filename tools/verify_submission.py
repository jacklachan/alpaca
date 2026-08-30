"""One command a judge can run to check this project's claims.

    python tools/verify_submission.py

It re-derives what can be re-derived and inspects what cannot. Nothing is
taken on trust, nothing is mutated, and no credential is required: every check
reads local artifacts only.

A `SKIP` is not a soft failure. This repository has legitimately not traded
yet, and a check that reported that absence as `FAIL` would be misreporting in
the same way an inflated claim would. What the exit code guarantees is
narrower and more useful: nothing we *did* record contradicts anything we say.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glassbox import config as C  # noqa: E402
from glassbox import verification as V  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)
_COLOUR = {V.PASS: GREEN, V.FAIL: RED, V.SKIP: YELLOW}


def tracked_files(root: Path) -> list[str]:
    # Include untracked-but-not-ignored files: a secret in a file that has
    # not been committed yet is still a secret about to be committed.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split() if result.returncode == 0 else []


def check_public_claims() -> V.CheckResult:
    """The claim tests are themselves evidence, so run them here."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_claims.py", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stdout or result.stderr).strip().splitlines()[-3:]
        return V.CheckResult("public claims match the code", V.FAIL, " ".join(tail))
    return V.CheckResult(
        "public claims match the code", V.PASS, "every documented claim is enforced by a test"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--out", default="", help="also write the JSON report here")
    parser.add_argument(
        "--skip-claims", action="store_true", help="do not shell out to the claim tests"
    )
    args = parser.parse_args(argv)

    extra = [] if args.skip_claims else [check_public_claims]
    report = V.run_all(
        ROOT,
        journal_path=Path(C.JOURNAL_PATH),
        manifest_path=ROOT / "state" / "release.json",
        ledger_path=Path(C.LEDGER_STATE_FILE),
        account_id="",
        environment="scored",
        tracked=tracked_files(ROOT),
        extra=extra,
    )

    payload = report.as_dict()
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if report.ok else 1

    print(f"\n{BOLD}Glassbox submission verification{RESET}")
    print(f"{DIM}Local artifacts only. No credentials, no network, nothing mutated.{RESET}\n")
    for result in report.results:
        colour = _COLOUR.get(result.status, "")
        print(f"  [{colour}{result.status}{RESET}] {result.name}")
        if result.detail:
            print(f"         {DIM}{result.detail}{RESET}")
    print()
    verdict = f"{GREEN}VERIFIED{RESET}" if report.ok else f"{RED}CONTRADICTION FOUND{RESET}"
    print(
        f"  {verdict}  {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} not yet applicable"
    )
    if report.skipped:
        print(
            f"  {DIM}A skip means the evidence does not exist yet -- not that a check "
            f"was waived.{RESET}"
        )
    print()
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
