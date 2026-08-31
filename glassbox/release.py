"""What is running, against which account, built from which source.

A release manifest answers one question a judge, an operator, or a later
version of us will ask: the evidence in the journal came from *what*? Without a
binding between the commit, the dependency locks, the policy that was in force,
and the account that was traded, a journal is a set of claims about an
unidentified program.

Two rules shape everything here.

Secrets never enter a manifest. The point of the file is that it can be shown
to someone, so it carries an account *suffix* and hashes of configuration, not
credentials, keys, or webhook URLs. `assert_no_secrets` is executable rather
than advisory because a redaction rule that is only written down is a rule that
eventually gets skipped.

`build()` creates only a local self-description; it cannot approve itself. A
scored start gains authority only when an external approved SHA and a fresh,
hashed evidence bundle match that description exactly. Alpaca order ids and
captured proof remain the independently verifiable facts behind those hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .state import atomic_write_json

SCHEMA_VERSION = 1

#: Only this exact normalized endpoint may ever appear in a scored manifest.
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"

#: External evidence that must be present before the scored process can start.
#: Required before any scored run, on any host. Each one protects the account
#: rather than the infrastructure: the journal is intact, the account is the
#: one we expect, the venue integration is real, and an order has actually been
#: submitted and reconciled once under a capped development proof.
CORE_RELEASE_CHECKS = (
    "journal_chain",
    "account_identity",
    "cli_proof",
    "development_venue_proof",
)

#: Required only when the agent is actually deployed. tools/soak.sh proves the
#: box stays up, that systemd restarts what it promised to restart, and that
#: the unit file is valid -- all of which are properties of a host, not of the
#: account. Demanding it when the agent runs locally gates scored startup on
#: infrastructure that is not in use, while the crash drill already covers the
#: recovery logic. Scoped, not deleted: deploy, and it is required again.
DEPLOYMENT_RELEASE_CHECKS = ("deployment_soak",)

#: Everything, for a deployed run. Kept as the name callers already import.
REQUIRED_RELEASE_CHECKS = CORE_RELEASE_CHECKS + DEPLOYMENT_RELEASE_CHECKS


def is_deployed(environment: Mapping[str, str] | None = None) -> bool:
    """True when this process is running as a deployed service.

    Two signals, either sufficient. GLASSBOX_DEPLOYMENT is the explicit one an
    operator sets; INVOCATION_ID is set by systemd for every unit it starts,
    which covers the path deploy/setup.sh actually installs.

    A VPS run started by hand, outside systemd and without the flag, is not
    detected -- so the flag is the mechanism to rely on. That is a deliberate
    limit rather than an oversight: the supported deployment path installs a
    systemd unit, and the alternative to this scoping was deleting the check
    outright.
    """
    env_map = environment if environment is not None else os.environ
    if (env_map.get("GLASSBOX_DEPLOYMENT") or "").strip().lower() in {"1", "true", "yes", "vps"}:
        return True
    return bool((env_map.get("INVOCATION_ID") or "").strip())


def required_release_checks(
    *, deployed: bool | None = None, environment: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """The checks a scored run must show, for where it is actually running."""
    if deployed is None:
        deployed = is_deployed(environment)
    return REQUIRED_RELEASE_CHECKS if deployed else CORE_RELEASE_CHECKS


RELEASE_EVIDENCE_MAX_AGE = timedelta(hours=24)
RELEASE_EVIDENCE_CLOCK_SKEW = timedelta(minutes=5)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Environment variables whose values must never be written anywhere.
SECRET_ENV_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "DISCORD_WEBHOOK_URL",
)

#: Substrings that indicate a credential leaked into the manifest body.
_SECRET_MARKERS = (
    "sk-ant-",
    "discord.com/api/webhooks",
    "-----begin",
)


class ReleaseError(RuntimeError):
    """The running release cannot be bound to a reviewable identity."""


def _run_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def git_commit(root: Path) -> str:
    return _run_git("rev-parse", "HEAD", cwd=root)


def git_is_dirty(root: Path) -> bool:
    return bool(_run_git("status", "--porcelain", cwd=root))


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def redact_account(account_id: str) -> str:
    """Judges get the full id through the submission channel, not from here."""
    if not account_id:
        return ""
    return f"...{account_id[-4:]}" if len(account_id) > 4 else "..."


def config_policy_hash(policy: Mapping[str, Any]) -> str:
    """Hash the policy that was in force, with no secret in the input."""
    body = json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def normalize_paper_endpoint(value: str) -> str:
    """Return the one allowed paper endpoint or fail on lookalike URLs."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ReleaseError(f"endpoint is not the paper endpoint: {value}") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "paper-api.alpaca.markets"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseError(f"endpoint is not the paper endpoint: {value}")
    return PAPER_ENDPOINT


def _parse_utc(value: Any, *, field_name: str) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(f"verification {field_name} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReleaseError(f"verification {field_name} has no timezone")
    return parsed.astimezone(timezone.utc)


def assert_no_secrets(payload: Mapping[str, Any], environment: Mapping[str, str]) -> None:
    """Fail if any live credential value appears anywhere in the manifest."""
    body = json.dumps(payload, sort_keys=True, default=str)
    lowered = body.lower()
    for key in SECRET_ENV_KEYS:
        value = (environment.get(key) or "").strip()
        # Short values would collide with ordinary text; a real key is long.
        if len(value) >= 8 and value in body:
            raise ReleaseError(f"manifest contains the value of {key}")
    for marker in _SECRET_MARKERS:
        if marker in lowered:
            raise ReleaseError(f"manifest contains a credential marker: {marker!r}")


@dataclass
class ReleaseManifest:
    """An immutable description of one running release."""

    commit: str
    dirty: bool
    python_version: str
    platform: str
    runtime_lock_sha256: str
    dev_lock_sha256: str
    config_policy_hash: str
    resolved_endpoint: str
    environment: str
    expected_account_suffix: str
    strategy_allowlist: tuple[str, ...]
    option_underlyings: tuple[str, ...]
    candidate_schema_version: int
    built_at: str = ""
    pending_gates: tuple[str, ...] = ()
    verification: dict[str, Any] = field(default_factory=dict)

    # -- validation ------------------------------------------------------------

    def validate(self) -> None:
        """Refuse anything that could point at the wrong account or venue."""
        normalize_paper_endpoint(self.resolved_endpoint)
        if not self.expected_account_suffix:
            raise ReleaseError("manifest has no expected account binding")
        if not _FULL_SHA.fullmatch(self.commit):
            raise ReleaseError(f"commit is not a full sha: {self.commit!r}")
        if not self.strategy_allowlist:
            raise ReleaseError("manifest has an empty strategy allowlist")

    def assert_scored_startable(
        self,
        *,
        approved_commit: str = "",
        now: datetime | None = None,
        evidence_max_age: timedelta = RELEASE_EVIDENCE_MAX_AGE,
        deployed: bool | None = None,
    ) -> None:
        """The gate a scored run must pass before it may place anything.

        `deployed` selects which evidence is demanded; None asks the
        environment. A deployed run must additionally show the soak, because
        there the host is a thing that can fail.
        """
        self.validate()
        if self.dirty:
            raise ReleaseError("refusing to start scored: the working tree is dirty")
        if self.environment != "scored":
            raise ReleaseError(f"manifest environment is {self.environment!r}, not 'scored'")
        forbidden = {s for s in self.strategy_allowlist if s not in {"event_vol"}}
        if forbidden:
            raise ReleaseError(f"scored allowlist is not options-only: {sorted(forbidden)}")
        if not _FULL_SHA.fullmatch(approved_commit) or approved_commit != self.commit:
            raise ReleaseError(
                "approved commit must be an explicit full SHA matching the release manifest"
            )
        if self.pending_gates:
            raise ReleaseError(f"pending release gates: {', '.join(self.pending_gates)}")

        proof = self.verification
        if not isinstance(proof, Mapping) or not proof:
            raise ReleaseError("release verification evidence is missing")
        if proof.get("status") != "RELEASE VERIFIED":
            raise ReleaseError("release verification evidence is not RELEASE VERIFIED")

        bindings: dict[str, Any] = {
            "commit": self.commit,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "dev_lock_sha256": self.dev_lock_sha256,
            "config_policy_hash": self.config_policy_hash,
            "expected_account_suffix": self.expected_account_suffix,
            "candidate_schema_version": self.candidate_schema_version,
        }
        for name, expected in bindings.items():
            if proof.get(name) != expected:
                raise ReleaseError(f"verification {name} does not match the release manifest")
        try:
            proof_endpoint = normalize_paper_endpoint(str(proof.get("resolved_endpoint", "")))
        except ReleaseError as exc:
            raise ReleaseError(
                "verification resolved_endpoint does not match the release manifest"
            ) from exc
        if proof_endpoint != normalize_paper_endpoint(self.resolved_endpoint):
            raise ReleaseError("verification resolved_endpoint does not match the release manifest")

        checked_at = _parse_utc(proof.get("verified_at"), field_name="verified_at")
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = current_time - checked_at
        if age < -RELEASE_EVIDENCE_CLOCK_SKEW:
            raise ReleaseError("release verification timestamp is in the future")
        if age > evidence_max_age:
            raise ReleaseError("release verification evidence is stale")

        checks = proof.get("checks")
        artifacts = proof.get("artifact_sha256")
        if not isinstance(checks, Mapping) or not isinstance(artifacts, Mapping):
            raise ReleaseError(
                "release verification evidence has no required checks or artifact hashes"
            )
        for name in required_release_checks(deployed=deployed):
            if checks.get(name) != "PASS":
                raise ReleaseError(f"required release check {name} is missing, skipped, or failed")
            digest = artifacts.get(name)
            if not isinstance(digest, str) or not _FULL_SHA256.fullmatch(digest):
                raise ReleaseError(f"required release check {name} has no valid artifact SHA-256")

    def with_verification(self, verification: Mapping[str, Any]) -> ReleaseManifest:
        """A copy carrying this evidence envelope.

        Returns a new manifest rather than mutating: the description of a
        checkout and the approval granted against it are different objects,
        and conflating them makes it easy to approve something by accident.
        """
        return replace(self, verification=dict(verification))

    # -- serialisation ---------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        body = {
            "schema_version": SCHEMA_VERSION,
            "commit": self.commit,
            "dirty": self.dirty,
            "python_version": self.python_version,
            "platform": self.platform,
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "dev_lock_sha256": self.dev_lock_sha256,
            "config_policy_hash": self.config_policy_hash,
            "resolved_endpoint": self.resolved_endpoint,
            "environment": self.environment,
            "expected_account_suffix": self.expected_account_suffix,
            "strategy_allowlist": list(self.strategy_allowlist),
            "option_underlyings": list(self.option_underlyings),
            "candidate_schema_version": self.candidate_schema_version,
            "built_at": self.built_at,
            "pending_gates": list(self.pending_gates),
            "verification": self.verification,
        }
        return {**body, "manifest_sha256": config_policy_hash(body)}

    def write(self, path: str | Path, *, environment: Mapping[str, str]) -> dict[str, Any]:
        payload = self.to_json()
        assert_no_secrets(payload, environment)
        atomic_write_json(path, payload)
        return payload

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ReleaseManifest:
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ReleaseError(
                f"manifest schema {raw.get('schema_version')!r} is not {SCHEMA_VERSION}"
            )
        stored = raw.get("manifest_sha256")
        body = {k: v for k, v in raw.items() if k != "manifest_sha256"}
        if stored != config_policy_hash(body):
            raise ReleaseError("manifest hash mismatch: the file was edited after it was built")
        return cls(
            commit=str(raw["commit"]),
            dirty=bool(raw["dirty"]),
            python_version=str(raw["python_version"]),
            platform=str(raw["platform"]),
            runtime_lock_sha256=str(raw["runtime_lock_sha256"]),
            dev_lock_sha256=str(raw["dev_lock_sha256"]),
            config_policy_hash=str(raw["config_policy_hash"]),
            resolved_endpoint=str(raw["resolved_endpoint"]),
            environment=str(raw["environment"]),
            expected_account_suffix=str(raw["expected_account_suffix"]),
            strategy_allowlist=tuple(raw.get("strategy_allowlist", ())),
            option_underlyings=tuple(raw.get("option_underlyings", ())),
            candidate_schema_version=int(raw.get("candidate_schema_version", 0)),
            built_at=str(raw.get("built_at", "")),
            pending_gates=tuple(raw.get("pending_gates", ())),
            verification=dict(raw.get("verification", {})),
        )

    def detect_drift(self, other: ReleaseManifest) -> tuple[str, ...]:
        """Name every field that would make evidence from `other` not ours."""
        drift = []
        for name in (
            "commit",
            "dirty",
            "runtime_lock_sha256",
            "dev_lock_sha256",
            "config_policy_hash",
            "environment",
            "expected_account_suffix",
        ):
            if getattr(self, name) != getattr(other, name):
                drift.append(name)
        if tuple(self.strategy_allowlist) != tuple(other.strategy_allowlist):
            drift.append("strategy_allowlist")
        if normalize_paper_endpoint(self.resolved_endpoint) != normalize_paper_endpoint(
            other.resolved_endpoint
        ):
            drift.append("resolved_endpoint")
        if tuple(self.option_underlyings) != tuple(other.option_underlyings):
            drift.append("option_underlyings")
        if self.candidate_schema_version != other.candidate_schema_version:
            drift.append("candidate_schema_version")
        return tuple(drift)


def build(
    *,
    root: Path,
    environment: str,
    resolved_endpoint: str,
    expected_account_id: str,
    strategy_allowlist: tuple[str, ...],
    option_underlyings: tuple[str, ...],
    candidate_schema_version: int,
    policy: Mapping[str, Any],
    built_at: str,
    pending_gates: tuple[str, ...] = (),
    verification: Mapping[str, Any] | None = None,
) -> ReleaseManifest:
    """Describe the release as it actually is on disk right now."""
    return ReleaseManifest(
        commit=git_commit(root),
        dirty=git_is_dirty(root),
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        runtime_lock_sha256=file_sha256(root / "requirements.lock"),
        dev_lock_sha256=file_sha256(root / "requirements-dev.lock"),
        config_policy_hash=config_policy_hash(policy),
        resolved_endpoint=resolved_endpoint,
        environment=environment,
        expected_account_suffix=redact_account(expected_account_id),
        strategy_allowlist=tuple(strategy_allowlist),
        option_underlyings=tuple(option_underlyings),
        candidate_schema_version=candidate_schema_version,
        built_at=built_at,
        pending_gates=tuple(pending_gates),
        verification=dict(verification or {}),
    )


def load_approved(
    path: str | Path,
    *,
    current: ReleaseManifest,
    approved_commit: str,
    now: datetime | None = None,
) -> ReleaseManifest:
    """Load external release approval and bind it to the checkout on disk."""
    manifest_path = Path(path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        approved = ReleaseManifest.from_json(raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReleaseError(f"cannot load approved release manifest {manifest_path}: {exc}") from exc

    approved.assert_scored_startable(approved_commit=approved_commit, now=now)
    drift = approved.detect_drift(current)
    if drift:
        raise ReleaseError(f"current checkout drift: {', '.join(drift)}")
    return approved
