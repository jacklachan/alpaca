"""Evidence for a scored release, assembled from artifacts that already exist.

The release gate demands proof that specific things were done: the journal is
intact, the account is the expected one, the venue integration is real, an
order has actually been placed and reconciled once. Until now nothing produced
that proof, so the gate was unpassable -- which is a safe failure, but a
permanent one.

The rule this module is built around: **it attests, it does not manufacture.**
Every check maps to a real file on disk, and its SHA-256 goes into the
manifest, so an approval names the exact bytes it was granted against. A check
with no artifact is MISSING and the manifest is refused. There is deliberately
no flag to assert a check passed without the evidence for it, because a gate
that can be waived by the person it constrains is decoration.

`journal_chain` is the one check computed here rather than read: chain
verification is a pure local operation, and its artifact is the journal itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PASS = "PASS"
FAIL = "FAIL"
MISSING = "MISSING"

#: Where each check's artifact lives, relative to the state directory.
DEFAULT_ARTIFACTS: dict[str, str] = {
    "account_identity": "account_proof.json",
    "cli_proof": "cli_proof.json",
    "development_venue_proof": "dev_venue_proof.json",
    "deployment_soak": "soak_proof.json",
}

#: The key each artifact must set truthfully for its check to pass.
_COMPLETION_KEYS: dict[str, tuple[str, ...]] = {
    "account_identity": ("matches_expected", "complete"),
    "cli_proof": ("complete",),
    "development_venue_proof": ("complete",),
    "deployment_soak": ("complete",),
}


def sha256_file(path: str | Path) -> str:
    """Content address of an evidence file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class EvidenceItem:
    """One check, its verdict, and the bytes that justify it."""

    name: str
    status: str
    artifact: str = ""
    sha256: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASS


def journal_chain_evidence(journal_path: str | Path) -> EvidenceItem:
    """Verify the hash chain. Computed, not read from a file.

    A journal that does not exist yet verifies clean with zero entries, which
    is the correct answer for a first scored start: there is nothing recorded,
    so nothing has been tampered with.
    """
    from .journal import Journal

    path = Path(journal_path)
    ok, why = Journal(path).verify()
    if not ok:
        return EvidenceItem("journal_chain", FAIL, str(path), "", why)

    if not path.exists():
        # Address the absence itself, so the approval still names exact bytes.
        digest = hashlib.sha256(b"").hexdigest()
        return EvidenceItem("journal_chain", PASS, str(path), digest, f"{why} (no journal yet)")
    return EvidenceItem("journal_chain", PASS, str(path), sha256_file(path), why)


def artifact_evidence(name: str, path: str | Path) -> EvidenceItem:
    """Read one captured proof bundle and judge it on its own claim."""
    target = Path(path)
    if not target.exists():
        return EvidenceItem(name, MISSING, str(target), "", "no artifact captured")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        return EvidenceItem(name, FAIL, str(target), "", f"unreadable: {exc}")
    if not isinstance(payload, Mapping):
        return EvidenceItem(name, FAIL, str(target), "", "artifact is not an object")

    for key in _COMPLETION_KEYS.get(name, ("complete",)):
        if payload.get(key) is not True:
            return EvidenceItem(
                name,
                FAIL,
                str(target),
                sha256_file(target),
                f"artifact does not report {key} is true",
            )
    return EvidenceItem(name, PASS, str(target), sha256_file(target), "captured and complete")


def collect(
    required: Iterable[str],
    *,
    state_dir: str | Path,
    journal_path: str | Path,
) -> list[EvidenceItem]:
    """Gather evidence for exactly the checks this run must show."""
    directory = Path(state_dir)
    items: list[EvidenceItem] = []
    for name in required:
        if name == "journal_chain":
            items.append(journal_chain_evidence(journal_path))
            continue
        artifact = DEFAULT_ARTIFACTS.get(name, f"{name}.json")
        items.append(artifact_evidence(name, directory / artifact))
    return items


def verification_block(
    manifest: Any,
    items: Iterable[EvidenceItem],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The evidence envelope the release gate reads.

    Carries the manifest bindings so an approval cannot be lifted onto a
    different commit, lock, policy, account or endpoint than the one it was
    granted for.
    """
    collected = list(items)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "status": "RELEASE VERIFIED" if all(i.ok for i in collected) else "RELEASE INCOMPLETE",
        "verified_at": stamp.isoformat().replace("+00:00", "Z"),
        "commit": manifest.commit,
        "runtime_lock_sha256": manifest.runtime_lock_sha256,
        "dev_lock_sha256": manifest.dev_lock_sha256,
        "config_policy_hash": manifest.config_policy_hash,
        "expected_account_suffix": manifest.expected_account_suffix,
        "candidate_schema_version": manifest.candidate_schema_version,
        "resolved_endpoint": manifest.resolved_endpoint,
        "checks": {i.name: i.status for i in collected},
        "artifact_sha256": {i.name: i.sha256 for i in collected if i.sha256},
        "artifacts": {i.name: i.artifact for i in collected},
        "details": {i.name: i.detail for i in collected},
    }
