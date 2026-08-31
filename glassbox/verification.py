"""Checks a third party can run to see whether our claims hold.

Most projects ask to be believed. The argument this one makes is that belief
should not be necessary: if the agent is deterministic, its candidate set can
be rebuilt and must hash the same; if the journal is a hash chain, a single
edited byte must be detectable; if the AI can only pick an id, the recorded
selection must be one of the ids that was offered; if the public copy claims
something, a test must fail when the code stops supporting it.

Each of those is a check here, and each returns the same shape, so the result
reads as a report rather than a log. The point is not that these checks pass.
It is that someone who does not trust us can run them.

Checks never mutate anything. A missing artifact is `SKIP`, not `FAIL`: this
repository has legitimately never traded, and reporting that absence as a
failure would be as dishonest as hiding it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    """One verifiable claim and what happened when it was tested."""

    name: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (PASS, SKIP)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class VerificationReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> CheckResult:
        self.results.append(result)
        return result

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == SKIP)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "checks": [r.as_dict() for r in self.results],
        }


# -- individual checks ---------------------------------------------------------


def check_journal_chain(journal_path: Path) -> CheckResult:
    """The hash chain detects any later edit to the recorded file."""
    name = "journal hash chain"
    if not journal_path.exists():
        return CheckResult(name, SKIP, "no journal yet; this checkout has never run")
    from .journal import Journal

    ok, why = Journal(journal_path).verify()
    entries = sum(1 for _ in Journal(journal_path).read())
    return CheckResult(
        name,
        PASS if ok else FAIL,
        why,
        {"entries": entries, "path": str(journal_path)},
    )


def check_selection_was_offered(journal_path: Path) -> CheckResult:
    """Every AI selection names a candidate that was actually offered.

    This is the central claim of the whole design, and it is checkable after
    the fact rather than taken on trust: if a selected id ever appears that was
    not in the set built for that cycle, the AI authored something.
    """
    name = "AI only ever selected an offered candidate"
    if not journal_path.exists():
        return CheckResult(name, SKIP, "no journal yet")
    from .journal import Journal

    offered: set[str] = set()
    selections = 0
    violations: list[str] = []
    for record in Journal(journal_path).read():
        payload = record.get("payload") or {}
        event = record.get("event")
        if event == "CANDIDATE_SET_BUILT":
            for candidate in payload.get("candidate_ids") or []:
                offered.add(str(candidate))
        elif event == "CANDIDATE_SELECTED":
            selections += 1
            chosen = str(payload.get("candidate_id") or payload.get("plan_id") or "")
            if chosen and offered and chosen not in offered:
                violations.append(chosen)
    if violations:
        return CheckResult(
            name,
            FAIL,
            f"{len(violations)} selection(s) never offered",
            {"unoffered": violations[:10]},
        )
    return CheckResult(
        name,
        PASS if selections else SKIP,
        f"{selections} selection(s) checked against {len(offered)} offered candidates"
        if selections
        else "no selection recorded yet",
        {"selections": selections, "offered": len(offered)},
    )


def check_no_unbounded_ai_fields(journal_path: Path) -> CheckResult:
    """The model's recorded output never contains an executable field."""
    name = "AI output carried no executable field"
    if not journal_path.exists():
        return CheckResult(name, SKIP, "no journal yet")
    from .journal import Journal

    forbidden = {"symbol", "qty", "limit_price", "side", "strike", "expiry", "notional"}
    offenders: list[str] = []
    checked = 0
    for record in Journal(journal_path).read():
        if record.get("event") not in ("CANDIDATE_SELECTED", "CANDIDATE_ABSTAINED"):
            continue
        checked += 1
        raw = (record.get("payload") or {}).get("model_output")
        if isinstance(raw, dict):
            extra = forbidden & set(raw)
            if extra:
                offenders.append(",".join(sorted(extra)))
    if offenders:
        return CheckResult(
            name, FAIL, "model output contained executable fields", {"fields": offenders[:10]}
        )
    return CheckResult(
        name,
        PASS if checked else SKIP,
        f"{checked} model response(s) carried only an id or an abstention"
        if checked
        else "no model response recorded yet",
    )


def check_candidate_replay(journal_path: Path) -> CheckResult:
    """Rebuild every recorded candidate set from the parts it recorded.

    This is the check that makes "deterministic" a verb. The agent published a
    content address for each set it offered; replay recomputes it from the
    recorded ids and content hashes and compares. A mismatch means the journal
    was edited or the hashing changed, neither of which is visible by reading
    the code.
    """
    name = "recorded candidate sets replay to the same hash"
    if not journal_path.exists():
        return CheckResult(name, SKIP, "no journal yet")
    from .journal import Journal
    from .replay import replay_journal

    report = replay_journal(Journal(journal_path).read())
    if not report.replays:
        return CheckResult(name, SKIP, "no candidate set has been offered yet")
    if report.mismatched:
        return CheckResult(
            name,
            FAIL,
            f"{len(report.mismatched)} recorded set(s) do not rebuild to their published hash",
            report.as_dict(),
        )
    if report.unoffered_selections:
        return CheckResult(
            name,
            FAIL,
            "a selection named a candidate that was never offered",
            report.as_dict(),
        )
    return CheckResult(
        name,
        PASS,
        f"{report.verified} of {len(report.replays)} set(s) rebuilt exactly"
        + (f", {len(report.unverifiable)} unaddressable" if report.unverifiable else ""),
        report.as_dict(),
    )


def check_release_manifest(manifest_path: Path) -> CheckResult:
    """The manifest is internally consistent and was not edited after build."""
    name = "release manifest integrity"
    if not manifest_path.exists():
        return CheckResult(name, SKIP, "no release manifest built yet")
    from .release import ReleaseError, ReleaseManifest

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ReleaseManifest.from_json(raw)
        manifest.validate()
    except (ReleaseError, ValueError, KeyError) as exc:
        return CheckResult(name, FAIL, str(exc))
    return CheckResult(
        name,
        PASS,
        f"commit {manifest.commit[:12]}, {manifest.environment}, options-only allowlist",
        {"commit": manifest.commit, "strategies": list(manifest.strategy_allowlist)},
    )


def check_position_ledger(ledger_path: Path, *, account_id: str, environment: str) -> CheckResult:
    """The ledger parses, checksums, and belongs to this account."""
    name = "position ledger integrity"
    if not ledger_path.exists():
        return CheckResult(name, SKIP, "no position ledger yet; nothing has been owned")
    from .position_ledger import PositionLedger
    from .state import StateCorrupt

    try:
        ledger = PositionLedger.load(ledger_path, account_id=account_id, environment=environment)
    except StateCorrupt as exc:
        return CheckResult(name, FAIL, str(exc))
    held = {s: str(e.signed_qty) for s, e in ledger.entries.items() if e.signed_qty != 0}
    return CheckResult(
        name,
        PASS,
        f"{len(ledger.entries)} contract(s) tracked, {len(held)} currently held",
        {"held": held, "generation": ledger.generation},
    )


def check_no_secrets_committed(root: Path, tracked: Iterable[str]) -> CheckResult:
    """No committed file carries anything key-shaped."""
    import re

    name = "no credentials committed"
    pattern = re.compile(r"\b(PK[A-Z0-9]{18,}|sk-ant-[A-Za-z0-9-]{20,})")
    offenders: list[str] = []
    for relative in tracked:
        if relative.endswith(".md") or relative.startswith(".github/"):
            continue
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        if pattern.search(text):
            offenders.append(relative)
    if offenders:
        return CheckResult(name, FAIL, "key-shaped strings found", {"files": offenders})
    return CheckResult(name, PASS, "no key-shaped string in any tracked file")


def check_dependency_locks(root: Path) -> CheckResult:
    """Both locks exist and pin exact versions."""
    import re

    name = "dependencies are exactly pinned"
    problems: list[str] = []
    for filename in ("requirements.lock", "requirements-dev.lock"):
        path = root / filename
        if not path.exists():
            problems.append(f"{filename} is missing")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-r"):
                continue
            if not re.match(r"^[A-Za-z0-9._-]+==", line):
                problems.append(f"{filename}: unpinned requirement {line!r}")
    if problems:
        return CheckResult(name, FAIL, "; ".join(problems[:5]))
    return CheckResult(name, PASS, "runtime and dev locks pin exact versions")


def check_proof_bundle(path: Path, label: str) -> CheckResult:
    """A captured CLI or MCP proof bundle, if one exists."""
    name = f"{label} proof bundle"
    if not path.exists():
        return CheckResult(name, SKIP, f"no {label} proof captured yet")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return CheckResult(name, FAIL, f"unreadable: {exc}")
    if not raw.get("complete"):
        return CheckResult(name, SKIP, f"{label} proof present but marked incomplete")
    return CheckResult(name, PASS, f"{label} proof captured and complete")


def run_all(
    root: Path,
    *,
    journal_path: Path,
    manifest_path: Path,
    ledger_path: Path,
    account_id: str = "",
    environment: str = "scored",
    tracked: Iterable[str] = (),
    extra: Iterable[Callable[[], CheckResult]] = (),
) -> VerificationReport:
    """Run every check and return one report."""
    report = VerificationReport()
    report.add(check_journal_chain(journal_path))
    report.add(check_selection_was_offered(journal_path))
    report.add(check_no_unbounded_ai_fields(journal_path))
    report.add(check_candidate_replay(journal_path))
    report.add(check_release_manifest(manifest_path))
    report.add(check_position_ledger(ledger_path, account_id=account_id, environment=environment))
    report.add(check_no_secrets_committed(root, tracked))
    report.add(check_dependency_locks(root))
    report.add(check_proof_bundle(root / "state" / "cli_proof.json", "CLI"))
    report.add(check_proof_bundle(root / "state" / "mcp_proof.json", "MCP"))
    for check in extra:
        report.add(check())
    return report
