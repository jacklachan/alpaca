"""Assemble the approved release manifest a scored run must load.

The release gate reads `state/release.json` and demands a verification block:
every required check PASS, each with the SHA-256 of the artifact that proves
it, bound to the exact commit, locks, policy, account and endpoint it was
approved against. Nothing produced that file, so scored mode was unreachable
regardless of what evidence existed.

This produces it, under one rule: **it attests, it does not manufacture.**
Every check must map to a real artifact on disk. A missing one is reported and
the manifest is refused. There is no override flag, because a gate the
constrained party can wave through is not a gate.

    python tools/build_release_manifest.py            # assemble, or explain why not
    python tools/build_release_manifest.py --dry-run  # show the verdict, write nothing

On success it prints the approved commit SHA to export, because the gate binds
the manifest to an explicitly approved commit rather than to whatever happens
to be checked out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glassbox import config as C  # noqa: E402
from glassbox import evidence as E  # noqa: E402
from glassbox.release import (  # noqa: E402
    ReleaseError,
    required_release_checks,
)
from glassbox.state import atomic_write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)
_COLOUR = {E.PASS: GREEN, E.FAIL: RED, E.MISSING: YELLOW}

#: How each missing artifact is produced, so a refusal is actionable.
_HOW_TO_CAPTURE = {
    "account_identity": "python tools/account_probe.py --emit state/account_proof.json",
    "cli_proof": "python tools/capture_alpaca_proof.py --out state/cli_proof.json",
    "development_venue_proof": (
        "python tools/live_check.py --trade --notional 25 --emit state/dev_venue_proof.json"
    ),
    "deployment_soak": "sudo bash tools/soak.sh   (deployed runs only)",
    "journal_chain": "the journal chain is broken; do not proceed, investigate it",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="state/release.json")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    parser.add_argument(
        "--deployed",
        action="store_true",
        help="also require deployment_soak (set when running as a service)",
    )
    args = parser.parse_args(argv)

    # The manifest describes this checkout. Building it needs the same
    # environment a scored run resolves, so a mismatch surfaces here rather
    # than at startup.
    import main as glassbox_main

    try:
        manifest = glassbox_main.release_manifest()
    except ReleaseError as exc:
        print(f"{RED}cannot describe this release:{RESET} {exc}", file=sys.stderr)
        return 2

    required = required_release_checks(deployed=args.deployed or None)
    items = E.collect(
        required,
        state_dir=Path(args.state_dir),
        journal_path=Path(C.JOURNAL_PATH),
    )
    verification = E.verification_block(manifest, items)

    print(
        f"\n{BOLD}Release evidence{RESET}  commit {manifest.commit[:12]}  "
        f"{manifest.environment}  account {manifest.expected_account_suffix}\n"
    )
    for item in items:
        colour = _COLOUR.get(item.status, "")
        print(f"  [{colour}{item.status}{RESET}] {item.name}")
        if item.detail:
            print(f"         {DIM}{item.detail}{RESET}")
        if not item.ok:
            print(f"         {DIM}capture it with: {_HOW_TO_CAPTURE.get(item.name, '?')}{RESET}")
    print()

    if verification["status"] != "RELEASE VERIFIED":
        outstanding = [i.name for i in items if not i.ok]
        print(
            f"  {RED}REFUSED{RESET}  {len(outstanding)} check(s) without evidence: "
            f"{', '.join(outstanding)}"
        )
        print(
            f"  {DIM}No manifest written. Capture the evidence above and run this again.{RESET}\n"
        )
        return 1

    # Prove the assembled manifest actually satisfies the gate before writing
    # it. Producing a file that the thing consuming it rejects would just move
    # the failure somewhere less obvious.
    approved = manifest.with_verification(verification)
    try:
        approved.assert_scored_startable(
            approved_commit=manifest.commit, deployed=args.deployed or None
        )
    except ReleaseError as exc:
        print(f"  {RED}REFUSED{RESET}  assembled manifest does not satisfy the gate: {exc}\n")
        return 1

    if args.dry_run:
        print(f"  {GREEN}WOULD VERIFY{RESET}  every required check has evidence")
        print(f"  {DIM}--dry-run: nothing written{RESET}\n")
        return 0

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    payload = approved.to_json()
    atomic_write_json(out, payload)

    print(f"  {GREEN}RELEASE VERIFIED{RESET}  written to {out}")
    print(f"\n  {BOLD}Start the scored run with:{RESET}")
    print("    export GLASSBOX_RELEASE_GATE=1")
    print(f"    export GLASSBOX_APPROVED_COMMIT_SHA={manifest.commit}")
    print("    python main.py --dry-run     # confirm, then drop --dry-run\n")
    print(
        f"  {DIM}Evidence is valid for 24 hours; rebuild after that or after any commit.{RESET}\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
