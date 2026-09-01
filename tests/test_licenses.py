"""Licensing and provenance closure.

The event requires a public, MIT-compliant repository. These tests keep two
claims honest: the project states its own license, and the notices file
actually describes the lock it is shipped with.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_notices  # noqa: E402


def test_repository_has_a_root_mit_license():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Copyright (c) 2026" in text
    assert "WITHOUT WARRANTY OF ANY KIND" in text


def test_third_party_notices_exist_and_cover_every_pinned_runtime_package():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    packages = build_notices.pinned_packages()
    assert packages, "the runtime lock pinned nothing"
    for name, version, _marker in packages:
        assert f"`{name}`" in notices, f"{name} is shipped but not in the notices"
        assert version in notices, f"{name} {version} is not the version described"


def test_notices_are_regenerated_from_the_current_lock():
    """A stale notices file asserts something specific and false."""
    assert build_notices.main(["--check"]) == 0


def test_no_runtime_dependency_carries_an_unreviewed_license():
    unexpected = []
    for name, _version, _marker in build_notices.pinned_packages():
        license_name = build_notices.license_of(name)
        if license_name.startswith("UNKNOWN"):
            continue  # environment-conditional pin, recorded as such in the notices
        # Split compound SPDX expressions so every part is reviewed. Otherwise
        # a package could pair an unreviewed license with a known one and pass.
        parts = [
            part.strip(" ()")
            for part in license_name.replace(" OR ", " AND ").split(" AND ")
            if part.strip(" ()")
        ]
        if any(part not in build_notices.ALLOWED_LICENSES for part in parts):
            unexpected.append((name, license_name))
    assert not unexpected, f"unreviewed licenses: {unexpected}"


def test_notices_record_that_reference_projects_were_not_copied():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "No code, UI, prompt, or asset from any of them is present" in notices
    assert "PolyForm" in notices


@pytest.mark.parametrize("path", ["LICENSE", "THIRD_PARTY_NOTICES.md"])
def test_legal_files_are_ascii(path: str):
    """Encoding corruption in a legal file is a real problem, not a cosmetic one."""
    (ROOT / path).read_text(encoding="utf-8").encode("ascii")


def test_the_notices_never_record_unknown_for_a_shipped_dependency():
    """A legal document must describe the software, not the machine that
    generated it. 'UNKNOWN (not installed in this environment)' means whoever
    regenerated the file was missing part of the lock."""
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    # A conditional pin -- exceptiongroup on 3.11+ -- is genuinely absent and
    # correctly recorded as such. Only an unconditional one is a defect: it
    # means the generating environment, not the shipped software, is being
    # described. Conditional rows carry their marker in italics.
    offenders = [
        line
        for line in notices.splitlines()
        if "not installed in this environment" in line and "_" not in line.split("|")[2]
    ]
    assert not offenders, (
        "these packages were recorded as UNKNOWN because the generating "
        f"environment lacked them: {offenders}"
    )


def test_the_generator_refuses_rather_than_degrading(monkeypatch):
    """The guard that stops it happening again: an unconditionally pinned
    package that is absent makes the generator refuse, not substitute a
    placeholder."""
    real = build_notices.metadata.metadata

    def absent(name):
        if name == "anthropic":
            raise build_notices.metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(build_notices.metadata, "metadata", absent)

    assert "anthropic" in build_notices.missing_unconditional_packages()
    assert build_notices.main(["--check"]) == 2


def test_environment_conditional_pins_may_legitimately_be_absent():
    """exceptiongroup is pinned for python_version < 3.11 and is correctly
    missing here. The guard must not fire on that."""
    conditional = [n for n, _v, marker in build_notices.pinned_packages() if marker]
    assert conditional, "no conditional pins found; has the lock format changed?"
    assert not set(conditional) & set(build_notices.missing_unconditional_packages())
