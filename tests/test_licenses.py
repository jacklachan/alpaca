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
        if license_name not in build_notices.ALLOWED_LICENSES:
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
