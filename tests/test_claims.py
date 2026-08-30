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
PUBLIC_DOCS = ("README.md", "SOCIAL.md", "PLAN.md", "DECISIONS.md")


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


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
