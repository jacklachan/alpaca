"""Capture read-only Alpaca CLI evidence.

The event requires the Alpaca Trading API plus either the MCP server or the
CLI. This tool supplies the CLI half as *evidence*, and the single most
important property it has is that it cannot become a second order path.

So the command builder is an allowlist, not a filter. A subcommand is run only
if it appears in READ_ONLY_COMMANDS; anything carrying a mutating token is
refused before a process is spawned, and refusal is an exception rather than a
skipped step. A tool that silently drops the dangerous half of its input is
worse than one that stops.

Output is redacted before it is stored, hashed so it can be cited, and written
atomically. A nonzero exit or unparseable JSON marks the proof incomplete
rather than absent: "we ran it and it failed" and "we never ran it" are
different claims and the manifest has to keep them apart.

Nothing here places, cancels, replaces, closes, or exercises anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

# This repository's .env only; see main.py for why the default search
# up the directory tree is unsafe here.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


from glassbox.release import SECRET_ENV_KEYS  # noqa: E402
from glassbox.state import atomic_write_json  # noqa: E402

SCHEMA_VERSION = 1

#: The only CLI invocations this tool may ever build.
READ_ONLY_COMMANDS: dict[str, tuple[str, ...]] = {
    "account": ("account", "get"),
    "clock": ("clock", "get"),
    # `config get` does not exist in CLI v0.0.14, and the commands that would
    # replace it (`version`, `doctor`, `profile list`) print human-readable
    # text rather than JSON, so they cannot be parsed, hashed and cited the way
    # this bundle requires. They also prove nothing the API calls below do not:
    # five authenticated read-only endpoints are the evidence that the CLI was
    # actually used against this account.
    "option_contracts": ("option", "contracts", "list"),
    "orders": ("order", "list", "--status", "all"),
    "positions": ("position", "list"),
}

#: Any of these anywhere in a built argv means we are about to mutate.
FORBIDDEN_TOKENS = (
    "place",
    "submit",
    "create",
    "buy",
    "sell",
    "cancel",
    "close",
    "replace",
    "exercise",
    "liquidate",
    "delete",
    "update",
    "set",
    "patch",
)


class ProofRefused(RuntimeError):
    """The requested capture would not have been read-only."""


def build_argv(binary: str, name: str) -> tuple[str, ...]:
    """Return the exact argv for one allowlisted read, or refuse."""
    if name not in READ_ONLY_COMMANDS:
        raise ProofRefused(f"{name!r} is not an allowlisted read-only command")
    argv = (binary, *READ_ONLY_COMMANDS[name])
    lowered = [part.lower() for part in argv[1:]]
    for token in FORBIDDEN_TOKENS:
        if any(token == part or part.startswith(f"{token}-") for part in lowered):
            raise ProofRefused(f"refusing {name!r}: argv contains mutating token {token!r}")
    return argv


def redact(text: str, environment: Mapping[str, str]) -> str:
    """Remove credential values and anything shaped like a key or webhook."""
    cleaned = text
    for key in SECRET_ENV_KEYS:
        value = (environment.get(key) or "").strip()
        if len(value) >= 8:
            cleaned = cleaned.replace(value, f"<redacted:{key}>")
    cleaned = re.sub(r"sk-ant-[A-Za-z0-9\-_]+", "<redacted:anthropic-key>", cleaned)
    # Stop at a quote or whitespace: swallowing the closing quote would make
    # the redacted payload unparseable.
    cleaned = re.sub(r"https://discord\.com/api/webhooks/[^\s\"']+", "<redacted:webhook>", cleaned)
    cleaned = re.sub(r"\b(PK|SK)[A-Z0-9]{12,}\b", "<redacted:alpaca-key>", cleaned)
    return cleaned


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ProofStep:
    """One captured read."""

    name: str
    argv: tuple[str, ...]
    exit_code: int
    complete: bool
    stdout_sha256: str
    stdout_redacted: str
    stderr_redacted: str
    parsed: Any = None
    captured_at: str = ""


@dataclass
class ProofBundle:
    environment: str
    expected_account_suffix: str
    steps: list[ProofStep] = field(default_factory=list)
    account_id_matches_expected: bool | None = None
    complete: bool = False
    captured_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "environment": self.environment,
            "expected_account_suffix": self.expected_account_suffix,
            "account_id_matches_expected": self.account_id_matches_expected,
            "complete": self.complete,
            "captured_at": self.captured_at,
            "steps": [{**asdict(s), "argv": list(s.argv)} for s in self.steps],
        }


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=60)


def assert_paper_profile(environment: Mapping[str, str]) -> None:
    """Refuse to capture anything against a non-paper configuration."""
    if (environment.get("ALPACA_PAPER_TRADE", "true") or "").lower() != "true":
        raise ProofRefused("ALPACA_PAPER_TRADE is not true")
    base = environment.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if "paper-api" not in base:
        raise ProofRefused(f"base URL is not the paper endpoint: {base}")


def assert_credentials(environment: Mapping[str, str]) -> None:
    """A partial credential bundle fails closed rather than half-running."""
    missing = [k for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY") if not environment.get(k)]
    if missing:
        raise ProofRefused(f"missing credentials: {', '.join(missing)}")


def capture(
    *,
    binary: str,
    names: Sequence[str],
    environment: Mapping[str, str],
    expected_account_id: str,
    runner: Runner | None = None,
    now: datetime | None = None,
) -> ProofBundle:
    """Run every allowlisted read and return a redacted, hashed bundle."""
    assert_paper_profile(environment)
    assert_credentials(environment)

    run = runner or _default_runner
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    bundle = ProofBundle(
        environment=environment.get("ALPACA_ENV", "dev"),
        expected_account_suffix=f"...{expected_account_id[-4:]}" if expected_account_id else "",
        captured_at=stamp,
    )

    for name in names:
        argv = build_argv(binary, name)
        result = run(argv)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        parsed: Any = None
        complete = result.returncode == 0
        if complete and stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                # Ran, but did not produce citable evidence.
                complete = False
        bundle.steps.append(
            ProofStep(
                name=name,
                argv=argv,
                exit_code=result.returncode,
                complete=complete,
                stdout_sha256=_sha256(stdout),
                stdout_redacted=redact(stdout, environment),
                stderr_redacted=redact(stderr, environment),
                parsed=json.loads(redact(json.dumps(parsed), environment))
                if parsed is not None
                else None,
                captured_at=stamp,
            )
        )

    account_step = next((s for s in bundle.steps if s.name == "account"), None)
    if account_step is not None and isinstance(account_step.parsed, dict):
        returned = str(
            account_step.parsed.get("account_number") or account_step.parsed.get("id") or ""
        )
        bundle.account_id_matches_expected = bool(
            expected_account_id and returned == expected_account_id
        )

    bundle.complete = bool(bundle.steps) and all(s.complete for s in bundle.steps)
    if bundle.account_id_matches_expected is False:
        bundle.complete = False
    return bundle


def write_bundle(bundle: ProofBundle, path: str | Path) -> dict[str, Any]:
    payload = bundle.to_json()
    atomic_write_json(path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="alpaca", help="Alpaca CLI binary")
    parser.add_argument("--out", default="state/cli_proof.json")
    parser.add_argument("--only", nargs="*", default=list(READ_ONLY_COMMANDS))
    args = parser.parse_args(argv)

    environment = dict(os.environ)
    expected_key = (
        "ALPACA_EXPECTED_SCORED_ACCOUNT_ID"
        if environment.get("ALPACA_ENV") == "scored"
        else "ALPACA_EXPECTED_DEV_ACCOUNT_ID"
    )
    try:
        bundle = capture(
            binary=args.binary,
            names=args.only,
            environment=environment,
            expected_account_id=environment.get(expected_key, ""),
        )
    except ProofRefused as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 2

    write_bundle(bundle, args.out)
    status = "COMPLETE" if bundle.complete else "INCOMPLETE"
    print(f"{status}  {len(bundle.steps)} read-only steps -> {args.out}")
    return 0 if bundle.complete else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
