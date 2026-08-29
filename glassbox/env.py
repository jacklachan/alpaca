"""Environment reading that fails closed.

WHY THIS FILE EXISTS
--------------------
systemd's EnvironmentFile parser and python-dotenv do not agree, and the
disagreement is silent, deployment-only, and dangerous.

systemd treats '#' as a comment ONLY at the start of a line. python-dotenv
strips inline comments from unquoted values. So this line:

    ALPACA_ENV=scored               # dev | scored

is `scored` when you run `python main.py` by hand, and
`scored               # dev | scored` when systemd starts the service. And
because `load_dotenv()` defaults to override=False, the systemd value is the
one the process actually sees -- dotenv reads the file and then declines to
replace what is already there.

The consequence was not cosmetic. `Broker.assert_ready()` gates the scored
account checks on `self.env == "scored"`. Under systemd that comparison was
False, so the "equity must be exactly 100000" and "account must be clean"
checks silently did not run -- on the one account where they matter, in the
one environment where nobody is watching.

Two defences, because either alone is insufficient:

  1. `clean()` strips inline comments and whitespace, so a value that survives
     a sloppy .env still compares correctly.
  2. `require_choice()` refuses to start on a value that is not recognised,
     rather than silently falling through to the safer-looking branch. A
     mistyped ALPACA_ENV must be a crash, never a shrug.

Sanitising alone would be worse than nothing: it would leave the broken .env
in place to bite something else later. So `preflight()` also fails the boot.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SENSITIVE = ("KEY", "SECRET", "TOKEN", "WEBHOOK", "PASSWORD")

# An unquoted value followed by whitespace then '#' -- systemd keeps it,
# dotenv drops it. Quoted values are left alone; both parsers respect quotes.
_INLINE_COMMENT = re.compile(r"\s+#.*$")


class EnvError(RuntimeError):
    """Configuration is wrong in a way that must stop the process."""


def clean(value: str | None) -> str:
    """Normalise one env value the way a careful reader would."""
    if value is None:
        return ""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return _INLINE_COMMENT.sub("", v).strip()


def get(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return clean(raw) if raw is not None else default


def require(name: str) -> str:
    v = get(name)
    if not v:
        raise EnvError(f"{name} is not set")
    return v


def require_choice(name: str, allowed: set[str], default: str | None = None) -> str:
    """Read a value that must be one of `allowed`. Never falls through."""
    raw = os.getenv(name)
    if raw is None:
        if default is not None:
            return default
        raise EnvError(f"{name} is not set (expected one of {sorted(allowed)})")

    v = clean(raw)
    if v not in allowed:
        detail = ""
        if clean(raw) != raw.strip():
            detail = (f"\n  The raw value was {raw!r}, which suggests an inline "
                      f"'#' comment in .env.\n  systemd keeps inline comments; "
                      f"move every comment onto its own line.")
        raise EnvError(
            f"{name}={v!r} is not one of {sorted(allowed)}.{detail}")
    return v


def _systemd_values(path: Path) -> dict[str, str]:
    """Replicate systemd EnvironmentFile semantics exactly."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _dotenv_values(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    return {k: (v or "") for k, v in dotenv_values(str(path)).items()}


def parity_report(path: str | Path = ".env") -> tuple[bool, list[str]]:
    """Do the two parsers agree about this file? Returns (ok, problems)."""
    p = Path(path)
    if not p.exists():
        return True, []

    problems: list[str] = []

    seen: dict[str, int] = {}
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in seen:
                problems.append(
                    f"{k} is defined twice (lines {seen[k]} and {i}); "
                    f"the later definition wins and the earlier one is dead text")
            seen[k] = i

    sysd, dot = _systemd_values(p), _dotenv_values(p)
    for key in sorted(set(sysd) | set(dot)):
        s, d = sysd.get(key, "<missing>"), dot.get(key, "<missing>")
        if s != d:
            redacted = any(t in key.upper() for t in SENSITIVE)
            shown = ("<redacted>", "<redacted>") if redacted else (s, d)
            problems.append(
                f"{key} parses differently: systemd sees [{shown[0]}], "
                f"dotenv sees [{shown[1]}]. Under systemd the systemd value "
                f"wins. Move the '#' comment onto its own line.")

    return (not problems), problems


def preflight(path: str | Path = ".env", strict: bool = True) -> list[str]:
    """Validate the environment before anything connects to a broker.

    Raises EnvError when the configuration would behave differently under
    systemd than it does by hand. That difference is exactly the class of bug
    that only shows up unattended at 03:00, so it is worth refusing to boot.
    """
    ok, problems = parity_report(path)

    paper = get("ALPACA_PAPER_TRADE", "")
    if paper != "true":
        problems.append(
            f"ALPACA_PAPER_TRADE={paper!r}; must be exactly 'true'")

    try:
        require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")
    except EnvError as exc:
        problems.append(str(exc))

    base = get("ALPACA_BASE_URL", "")
    if base and "paper-api" not in base:
        problems.append(f"ALPACA_BASE_URL={base!r} is not the paper endpoint")

    if problems and strict:
        raise EnvError(
            "environment preflight failed:\n  - " + "\n  - ".join(problems))
    return problems
