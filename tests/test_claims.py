"""Public claims, checked against the code that has to support them.

A README is the one artifact judges read before anything else, and it is the
easiest place for a claim to outlive the thing it described. These tests fail
when the prose says more than the repository can demonstrate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
PUBLIC_DOCS = ("README.md", "SOCIAL.md", "PLAN.md", "DECISIONS.md", "docs/WRITEUP.md")


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")  # accepts nested paths


# -- what the AI is allowed to be described as doing ---------------------------


def test_public_copy_describes_bounded_selection_not_trade_generation():
    lowered = README.lower()
    assert "select" in lowered and "abstain" in lowered
    for forbidden in (
        "the model writes the trade",
        "the model proposes the trade",
        "ai generates the trade",
        "the ai decides what to buy",
    ):
        assert forbidden not in lowered, f"README claims {forbidden!r}"


def test_public_copy_states_the_model_cannot_invent_executable_fields():
    assert "cannot invent" in README.lower()


# -- claims that require evidence this repository does not have ----------------


@pytest.mark.parametrize("doc", PUBLIC_DOCS)
def test_public_copy_does_not_claim_tamper_proof_or_live_deployment(doc: str):
    """Only an affirmative claim fails. Explicitly disclaiming tamper-proofness
    is exactly what these documents should be doing."""
    lowered = _doc(doc).lower()
    affirmative = re.compile(
        r"(?<!not )(?<!never )(?<!not called )(?<!, )\b(is|are)\s+tamper[- ]proof\b"
    )
    assert not affirmative.search(lowered), f"{doc} affirmatively claims tamper-proof"
    assert "immutable audit log" not in lowered, f"{doc} claims an immutable audit log"
    # The disclaimer itself must survive: never silently drop it.
    if "tamper" in lowered:
        assert "not" in lowered or "reconcilable" in lowered


@pytest.mark.parametrize("doc", PUBLIC_DOCS)
def test_public_copy_does_not_claim_unproven_integrations(doc: str):
    """MCP is optional and was never demonstrated here. Saying otherwise is the
    single easiest disqualifying claim to make by accident."""
    lowered = _doc(doc).lower()
    if "mcp" not in lowered:
        return
    for forbidden in (
        "mcp integration is complete",
        "integrated the alpaca mcp server",
        "via the mcp server",
        "using the mcp server",
    ):
        assert forbidden not in lowered, f"{doc} claims {forbidden!r}"


@pytest.mark.parametrize("doc", PUBLIC_DOCS)
def test_public_copy_does_not_claim_realized_pnl_or_a_completed_soak(doc: str):
    lowered = _doc(doc).lower()
    for forbidden in ("realized p&l of", "soak complete", "soak passed", "deployed to production"):
        assert forbidden not in lowered, f"{doc} claims {forbidden!r}"


def test_readme_keeps_the_external_gates_visible():
    lowered = README.lower()
    assert "pending" in lowered
    assert "no live paper order" in lowered or "no paper order" in lowered


# -- claims that must match the code -------------------------------------------


def test_readme_invariant_count_matches_the_kernel():
    from glassbox.kernel import RiskKernel

    implemented = len([n for n in dir(RiskKernel) if re.fullmatch(r"_check_\d+_.*", n)])
    stated = re.search(r"(\d+)-invariant", README)
    assert stated, "README no longer states an invariant count"
    assert int(stated.group(1)) == implemented, (
        f"README says {stated.group(1)} invariants, kernel implements {implemented}"
    )


def test_readme_repository_map_only_lists_modules_that_exist():
    block = README.split("## Repository map", 1)[1]
    for match in re.finditer(r"^\s{2}([a-z_]+/?[a-z_]*\.py)", block, re.MULTILINE):
        name = match.group(1)
        assert (ROOT / "glassbox" / name).exists() or (ROOT / name).exists(), (
            f"README maps {name}, which does not exist"
        )


def test_readme_documents_the_licence_and_notices():
    assert "LICENSE" in README or "MIT" in README
    assert (ROOT / "LICENSE").exists()
    assert (ROOT / "THIRD_PARTY_NOTICES.md").exists()


def test_readme_does_not_hand_maintain_a_test_count():
    """Counts drift silently. Generate them or omit them."""
    assert not re.search(r"\b\d{2,4}\s+tests?\s+pass", README.lower())


# -- repository hygiene --------------------------------------------------------

#: The exact pattern .github/workflows/ci.yml scans committed files for.
_CI_CREDENTIAL_PATTERN = re.compile(r"\b(PK[A-Z0-9]{18,}|sk-ant-[A-Za-z0-9-]{20,})")


def test_no_committed_file_looks_like_a_live_credential():
    """Run CI's committed-credential scan here, portably.

    CI uses GNU grep; macOS git grep treats `\\b` differently and silently
    matched nothing, so this gate passed locally and failed on the runner. In
    Python the behaviour is the same everywhere, which is the point.
    """
    import subprocess

    # --cached --others --exclude-standard, not plain ls-files: a brand-new
    # file is untracked, so a tracked-only scan passes right up until the
    # moment the file is committed -- which is exactly when it matters.
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    offenders = []
    for name in tracked:
        if name.endswith(".md") or name.startswith(".github/"):
            continue  # the same exclusions CI applies
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        for match in _CI_CREDENTIAL_PATTERN.finditer(text):
            offenders.append(f"{name}: {match.group(0)[:12]}...")

    assert not offenders, (
        "these would fail CI's committed-credential scan; build test fixtures "
        f"at runtime instead of as literals: {offenders}"
    )


def test_every_github_action_is_pinned_to_a_commit_sha():
    """A tag is mutable. Whoever controls it can change what runs inside a
    workflow that holds repository credentials, so pins are 40-hex SHAs."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert uses, "no actions found; did the workflow move?"

    floating = [u for u in uses if not re.search(r"@[0-9a-f]{40}$", u)]
    assert not floating, f"these actions are not SHA-pinned: {floating}"


def test_every_action_pin_records_the_release_it_came_from():
    """A bare SHA is unreviewable. The trailing comment is what makes a bump
    auditable rather than a 40-character diff nobody can evaluate."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for line in workflow.splitlines():
        if "uses:" in line and "@" in line:
            assert re.search(r"#\s*v[\d.]+", line), f"pin has no version comment: {line.strip()}"


# -- the submission write-up ---------------------------------------------------

WRITEUP = ROOT / "docs" / "WRITEUP.md"


def test_the_required_one_page_writeup_exists_and_covers_all_three_topics():
    """The event requires a write-up covering AI logic, risk gates, and Alpaca
    infrastructure. Missing one is a missing submission requirement."""
    text = WRITEUP.read_text(encoding="utf-8").lower()
    assert "ai logic" in text
    assert "risk gate" in text
    assert "alpaca infrastructure" in text


def test_the_writeup_test_count_matches_the_real_suite():
    """A number in the document judges read must be true, and stay true. If
    this fails, the suite changed -- update the write-up, do not delete this."""
    import subprocess
    import sys

    text = WRITEUP.read_text(encoding="utf-8")
    stated = re.search(r"([\d,]+)\s+automated tests", text)
    assert stated, "the write-up no longer states a test count"
    claimed = int(stated.group(1).replace(",", ""))

    collected = subprocess.run(
        # sys.executable, not "python": a different interpreter on PATH has
        # different dependencies and silently collects a different suite.
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    found = re.search(r"(\d+) tests collected", collected.stdout)
    assert found, f"could not count the suite: {collected.stdout[-400:]}"
    actual = int(found.group(1))

    assert claimed == actual, (
        f"docs/WRITEUP.md claims {claimed} automated tests, the suite has {actual}"
    )


def test_the_writeup_keeps_its_unproven_gates_visible():
    """A write-up that drops the 'not claimed' section is the single easiest
    way this project becomes dishonest."""
    text = WRITEUP.read_text(encoding="utf-8").lower()
    assert "no live paper order" in text
    assert "no mcp integration" in text
