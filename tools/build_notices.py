"""Regenerate THIRD_PARTY_NOTICES.md from the runtime lock.

Hand-maintained notices drift the moment a lock changes, and a stale notice
file is worse than none: it asserts something specific and false. So the file
is generated, and tests/test_licenses.py fails when it no longer matches the
lock it claims to describe.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
LOCK = ROOT / "requirements.lock"

#: Licenses we are willing to ship. Anything else must be a deliberate,
#: recorded decision rather than a silent addition.
#: Compound SPDX expressions are split on AND/OR, and every part must appear
#: here, so a package cannot pass review by pairing an unreviewed license with
#: reviewed ones.
ALLOWED_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "Apache Software License",
    "Apache Software License v2",
    "BSD-3-Clause",
    "BSD License",
    "CC0-1.0",
    "MIT",
    "MIT License",
    "MIT OR Apache-2.0",
    "MPL-2.0",
    "PSF-2.0",
    "Zlib",
    "Dual License",
}

#: Reviewed license per pinned package, checked in deliberately.
#:
#: These were resolved from installed metadata once and normalised to SPDX,
#: then committed. They are NOT read from the environment at generation
#: time, because installed metadata is not stable across machines and this
#: file is a committed artifact that CI regenerates and compares:
#:
#:   exceptiongroup is pinned `python_version < "3.11"`, so it installs on
#:   some interpreters and not others -- present it reads MIT, absent it
#:   reads UNKNOWN.
#:
#:   numpy's Linux wheel publishes no License-Expression and falls back to
#:   the classifier "BSD License", while the Windows wheel declares the
#:   full SPDX compound.
#:
#: Either divergence makes the notices stale on every push from a machine
#: unlike the runner. A committed generated file has to be a pure function
#: of committed inputs, so the license comes from here and the lock alone.
#: Adding a dependency means adding its reviewed license here, which is the
#: review this file exists to record.
REVIEWED_LICENSES: dict[str, str] = {
    "alpaca-py": "Apache-2.0",
    "annotated-doc": "MIT",
    "annotated-types": "MIT",
    "anthropic": "MIT",
    "anyio": "MIT",
    "APScheduler": "MIT",
    "certifi": "MPL-2.0",
    "charset-normalizer": "MIT",
    "click": "BSD-3-Clause",
    "docstring-parser": "MIT",
    "exceptiongroup": "MIT",
    "fastapi": "MIT",
    "h11": "MIT",
    "httpcore": "BSD-3-Clause",
    "httpcore2": "BSD-3-Clause",
    "httpx": "BSD-3-Clause",
    "httpx2": "BSD-3-Clause",
    "idna": "BSD-3-Clause",
    "jiter": "MIT",
    "msgpack": "Apache-2.0",
    "numpy": "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
    "openai": "Apache-2.0",
    "pandas": "BSD-3-Clause",
    "pydantic": "MIT",
    "pydantic-core": "MIT",
    "python-dateutil": "Dual License",
    "python-dotenv": "BSD-3-Clause",
    "pytz": "MIT",
    "requests": "Apache-2.0",
    "six": "MIT",
    "sniffio": "MIT OR Apache-2.0",
    "sseclient-py": "Apache-2.0",
    "starlette": "BSD-3-Clause",
    "truststore": "MIT",
    "typing-extensions": "PSF-2.0",
    "typing-inspection": "MIT",
    "tzdata": "Apache-2.0",
    "tzlocal": "MIT",
    "urllib3": "MIT",
    "uvicorn": "BSD-3-Clause",
    "websockets": "BSD-3-Clause",
}


_PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^;]+)")


def pinned_packages(lock_path: Path = LOCK) -> list[tuple[str, str, str]]:
    """Return (name, version, marker) for every pin in the runtime lock."""
    out: list[tuple[str, str, str]] = []
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        match = _PIN.match(line)
        if not match:
            continue
        marker = line.split(";", 1)[1].strip() if ";" in line else ""
        out.append((match.group(1), match.group(2).strip(), marker))
    return sorted(out, key=lambda r: r[0].lower())


def license_of(name: str) -> str:
    """The reviewed license for a pinned package.

    Deterministic by construction: it never consults the environment. See
    REVIEWED_LICENSES for why.
    """
    reviewed = REVIEWED_LICENSES.get(name)
    if reviewed is not None:
        return reviewed
    return f"UNREVIEWED ({name} is not in REVIEWED_LICENSES; add it)"


def observed_license_of(name: str) -> str:
    """What the installed distribution claims. Advisory only -- use this to
    seed a REVIEWED_LICENSES entry, never to generate the notices."""
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "UNKNOWN (not installed in this environment)"
    declared = meta.get("License-Expression") or meta.get("License") or ""
    # A declared SPDX expression is authoritative however long it is. The old
    # 40-character cutoff dropped numpy's 48-character compound expression
    # through to the classifier fallback, and numpy publishes no license
    # classifiers, so it resolved to UNKNOWN. That made this generated file
    # depend on which wheel the local interpreter installed -- so it was stale
    # in CI on every push from a machine unlike the runner, which is exactly
    # what broke the pipeline.
    #
    # The cutoff existed to reject packages that paste their entire license
    # text into the License field. Detect that directly: real text is
    # multi-line and far longer than any expression.
    if declared and "\n" not in declared and len(declared) <= 120:
        return declared.strip()
    classifiers = [
        c.split(" :: ")[-1]
        for c in (meta.get_all("Classifier") or [])
        if c.startswith("License ::")
    ]
    return "; ".join(classifiers) if classifiers else "UNKNOWN"


def render(rows: Iterable[tuple[str, str, str, str]]) -> str:
    lines = [
        "# Third-party notices",
        "",
        "Glassbox depends on the packages below. Each is used under its own",
        "license, reproduced by its project; this file records what is shipped and",
        "under which terms. It is generated by `tools/build_notices.py` from",
        "`requirements.lock` -- edit the lock, then regenerate.",
        "",
        "| Package | Version | License |",
        "| --- | --- | --- |",
    ]
    for name, version, license_name, marker in rows:
        suffix = f" _{marker}_" if marker else ""
        lines.append(f"| `{name}` | {version}{suffix} | {license_name} |")
    lines += [
        "",
        "## Ideas and interfaces",
        "",
        "The official Alpaca repositories were read during design and are cited",
        "rather than copied:",
        "",
        "- `alpaca-py` (Apache-2.0) is the sole programmatic Trading/Data SDK.",
        "- The official Alpaca MCP server (MIT) was inspected for its tool surface.",
        "  No MCP integration is claimed here; see README for what is demonstrated.",
        "- Alpaca Skills (Apache-2.0) are prose guidance. Any wording adapted from",
        "  them belongs in operator documentation with attribution, never as",
        "  executable policy.",
        "",
        "Four third-party trading projects were reviewed during the design audit.",
        "No code, UI, prompt, or asset from any of them is present in this",
        "repository. Two of the four carry licenses that would forbid such reuse",
        "(one is unlicensed and self-described as proprietary; one is PolyForm",
        "Noncommercial 1.0.0), which is why every pattern here was implemented",
        "independently from the deterministic contracts in `glassbox/`.",
        "",
    ]
    return "\n".join(lines)


def build() -> str:
    rows = [(n, v, license_of(n), m) for n, v, m in pinned_packages()]
    return render(rows)


def missing_unconditional_packages() -> list[str]:
    """Pinned packages that are absent but should not be.

    A package with an environment marker -- `exceptiongroup` on 3.11+, the
    win32-only ones -- is legitimately absent here. An unconditional pin that
    is missing means the notices would record "UNKNOWN (not installed)" for a
    dependency we actually ship, which is how a legal document ends up
    describing the machine that generated it rather than the software.
    """
    absent: list[str] = []
    for name, _version, marker in pinned_packages():
        if marker:
            continue
        try:
            metadata.metadata(name)
        except metadata.PackageNotFoundError:
            absent.append(name)
    return absent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)

    absent = missing_unconditional_packages()
    if absent:
        print(
            "refusing to touch the notices: these pinned packages are not "
            f"installed, so their licenses would be recorded as UNKNOWN: {', '.join(absent)}\n"
            "install the full lock first: pip install -r requirements-dev.lock",
            file=sys.stderr,
        )
        return 2

    content = build()
    if args.check:
        current = NOTICES.read_text(encoding="utf-8") if NOTICES.exists() else ""
        if current != content:
            # Print what differs. "Stale" alone is unactionable when the file is
            # committed from one machine and checked on another: the whole
            # question is which package resolved differently and where.
            import difflib

            diff = difflib.unified_diff(
                current.splitlines(),
                content.splitlines(),
                fromfile="THIRD_PARTY_NOTICES.md (committed)",
                tofile="regenerated here",
                lineterm="",
                n=0,
            )
            print("THIRD_PARTY_NOTICES.md is stale; run tools/build_notices.py", file=sys.stderr)
            for line in diff:
                print(line, file=sys.stderr)
            return 1
        print("notices are current")
        return 0

    NOTICES.write_text(content, encoding="utf-8")
    print(f"wrote {NOTICES.name}: {len(pinned_packages())} packages")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
