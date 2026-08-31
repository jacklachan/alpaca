"""Evidence assembly for the release gate.

The gate demands proof that specific things were done. This module produces
that proof, so the property that matters is what it *refuses* to produce: a
check with no artifact must never come out as PASS, and there must be no way
to assert one without the bytes behind it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from glassbox import evidence as E
from glassbox.journal import Journal

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def artifact(tmp_path, name: str, payload: dict):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# -- it attests, it does not manufacture ---------------------------------------


def test_a_missing_artifact_is_missing_not_passing(tmp_path):
    item = E.artifact_evidence("cli_proof", tmp_path / "absent.json")
    assert item.status == E.MISSING
    assert item.ok is False
    assert item.sha256 == "", "a hash was produced for bytes that do not exist"


def test_an_artifact_that_does_not_claim_completion_fails(tmp_path):
    path = artifact(tmp_path, "cli_proof.json", {"complete": False})
    item = E.artifact_evidence("cli_proof", path)
    assert item.status == E.FAIL
    assert "complete" in item.detail


def test_an_unreadable_artifact_fails_rather_than_being_ignored(tmp_path):
    path = tmp_path / "cli_proof.json"
    path.write_text("{not json", encoding="utf-8")
    assert E.artifact_evidence("cli_proof", path).status == E.FAIL


def test_a_complete_artifact_passes_and_is_content_addressed(tmp_path):
    path = artifact(tmp_path, "cli_proof.json", {"complete": True})
    item = E.artifact_evidence("cli_proof", path)
    assert item.status == E.PASS
    assert item.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_account_identity_needs_the_account_to_actually_match(tmp_path):
    """Completion is not enough here: the artifact must say the returned
    account was the expected one."""
    wrong = artifact(tmp_path, "account_proof.json", {"matches_expected": False, "complete": True})
    assert E.artifact_evidence("account_identity", wrong).status == E.FAIL

    right = artifact(tmp_path, "account_proof.json", {"matches_expected": True, "complete": True})
    assert E.artifact_evidence("account_identity", right).status == E.PASS


# -- the journal chain is computed, not read -----------------------------------


def test_an_absent_journal_verifies_clean_for_a_first_run(tmp_path):
    """Nothing recorded means nothing tampered with. A first scored start must
    not be blocked on evidence that only exists after it has run."""
    item = E.journal_chain_evidence(tmp_path / "none.jsonl")
    assert item.status == E.PASS
    assert item.sha256, "even an absence should be content-addressed"


def test_an_intact_journal_passes_and_hashes_the_real_file(tmp_path):
    path = tmp_path / "j.jsonl"
    Journal(path).append("t", "STARTUP", {"equity": "100000"})
    item = E.journal_chain_evidence(path)
    assert item.status == E.PASS
    assert item.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_broken_chain_fails(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.append("t", "STARTUP", {"equity": "100000"})
    j.append("t", "HEARTBEAT", {"equity": "101000"})
    lines = path.read_text().splitlines()
    record = json.loads(lines[0])
    record["payload"]["equity"] = "999999"
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert E.journal_chain_evidence(path).status == E.FAIL


# -- the envelope --------------------------------------------------------------


def _manifest():
    return SimpleNamespace(
        commit="a" * 40,
        runtime_lock_sha256="r" * 64,
        dev_lock_sha256="d" * 64,
        config_policy_hash="c" * 64,
        expected_account_suffix="...9012",
        candidate_schema_version=1,
        resolved_endpoint="https://paper-api.alpaca.markets",
    )


def test_the_envelope_is_verified_only_when_every_check_passed():
    passing = [E.EvidenceItem("a", E.PASS, "x", "h"), E.EvidenceItem("b", E.PASS, "y", "h2")]
    assert E.verification_block(_manifest(), passing, now=NOW)["status"] == "RELEASE VERIFIED"

    mixed = passing + [E.EvidenceItem("c", E.MISSING)]
    assert E.verification_block(_manifest(), mixed, now=NOW)["status"] == "RELEASE INCOMPLETE"


def test_the_envelope_carries_the_manifest_bindings():
    """So an approval cannot be lifted onto a different commit or account."""
    block = E.verification_block(_manifest(), [E.EvidenceItem("a", E.PASS, "x", "h")], now=NOW)
    assert block["commit"] == "a" * 40
    assert block["expected_account_suffix"] == "...9012"
    assert block["resolved_endpoint"] == "https://paper-api.alpaca.markets"
    assert block["verified_at"].endswith("Z")


def test_only_checks_with_real_bytes_get_an_artifact_hash():
    block = E.verification_block(
        _manifest(),
        [E.EvidenceItem("has", E.PASS, "x", "h"), E.EvidenceItem("none", E.MISSING)],
        now=NOW,
    )
    assert "has" in block["artifact_sha256"]
    assert "none" not in block["artifact_sha256"], "a missing check was given a hash"


def test_collect_gathers_exactly_the_requested_checks(tmp_path):
    artifact(tmp_path, "cli_proof.json", {"complete": True})
    items = E.collect(
        ("journal_chain", "cli_proof", "deployment_soak"),
        state_dir=tmp_path,
        journal_path=tmp_path / "none.jsonl",
    )
    assert [i.name for i in items] == ["journal_chain", "cli_proof", "deployment_soak"]
    assert items[0].ok and items[1].ok
    assert items[2].status == E.MISSING


@pytest.mark.parametrize("check", sorted(E.DEFAULT_ARTIFACTS))
def test_every_artifact_backed_check_has_a_completion_rule(check: str):
    """A check with no completion rule would pass on any file at all."""
    assert check in E._COMPLETION_KEYS


# -- the assembled manifest must satisfy the gate it is built for --------------


def test_an_assembled_manifest_passes_the_real_release_gate(tmp_path, monkeypatch):
    """The end-to-end property: evidence in, a manifest the gate accepts out.
    Producing a file the consumer rejects would just move the failure
    somewhere less obvious."""
    from glassbox.release import ReleaseManifest, required_release_checks

    manifest = ReleaseManifest(
        commit="a" * 40,
        dirty=False,
        python_version="3.12.7",
        platform="test",
        runtime_lock_sha256="r" * 64,
        dev_lock_sha256="d" * 64,
        config_policy_hash="c" * 64,
        resolved_endpoint="https://paper-api.alpaca.markets",
        environment="scored",
        expected_account_suffix="...9012",
        strategy_allowlist=("event_vol",),
        option_underlyings=("SPY", "QQQ"),
        candidate_schema_version=1,
    )

    artifact(tmp_path, "account_proof.json", {"matches_expected": True, "complete": True})
    artifact(tmp_path, "cli_proof.json", {"complete": True})
    artifact(tmp_path, "dev_venue_proof.json", {"traded": True, "complete": True})

    items = E.collect(
        required_release_checks(deployed=False),
        state_dir=tmp_path,
        journal_path=tmp_path / "none.jsonl",
    )
    approved = manifest.with_verification(E.verification_block(manifest, items))

    # The gate accepts it. This is the whole point of the module.
    approved.assert_scored_startable(approved_commit="a" * 40, deployed=False)


def test_a_manifest_assembled_without_evidence_is_rejected_by_the_gate(tmp_path):
    """The refusal has to hold at the gate too, not just in the builder."""
    from glassbox.release import ReleaseError, ReleaseManifest, required_release_checks

    manifest = ReleaseManifest(
        commit="a" * 40,
        dirty=False,
        python_version="3.12.7",
        platform="test",
        runtime_lock_sha256="r" * 64,
        dev_lock_sha256="d" * 64,
        config_policy_hash="c" * 64,
        resolved_endpoint="https://paper-api.alpaca.markets",
        environment="scored",
        expected_account_suffix="...9012",
        strategy_allowlist=("event_vol",),
        option_underlyings=("SPY", "QQQ"),
        candidate_schema_version=1,
    )
    items = E.collect(
        required_release_checks(deployed=False),
        state_dir=tmp_path,  # empty: no artifacts at all
        journal_path=tmp_path / "none.jsonl",
    )
    approved = manifest.with_verification(E.verification_block(manifest, items))

    with pytest.raises(ReleaseError):
        approved.assert_scored_startable(approved_commit="a" * 40, deployed=False)


def test_with_verification_returns_a_copy_rather_than_mutating():
    """A description of a checkout and an approval granted against it are
    different objects; conflating them makes accidental approval easy."""
    from glassbox.release import ReleaseManifest

    base = ReleaseManifest(
        commit="a" * 40,
        dirty=False,
        python_version="3.12.7",
        platform="test",
        runtime_lock_sha256="r" * 64,
        dev_lock_sha256="d" * 64,
        config_policy_hash="c" * 64,
        resolved_endpoint="https://paper-api.alpaca.markets",
        environment="scored",
        expected_account_suffix="...9012",
        strategy_allowlist=("event_vol",),
        option_underlyings=("SPY", "QQQ"),
        candidate_schema_version=1,
    )
    approved = base.with_verification({"status": "RELEASE VERIFIED"})
    assert base.verification == {}
    assert approved is not base
    assert approved.verification["status"] == "RELEASE VERIFIED"
