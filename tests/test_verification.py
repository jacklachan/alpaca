"""The checks a third party runs against us.

A verifier that only ever passes is decoration. Every check here is tested
both ways: it passes on good evidence, and it actually fails on the specific
corruption it exists to catch.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from glassbox import verification as V
from glassbox.journal import Journal
from glassbox.position_ledger import PositionLedger

CALL = "SPY260904C00600000"


def journal_at(path: Path, records) -> Path:
    j = Journal(path)
    for actor, event, payload in records:
        j.append(actor, event, payload)
    return path


# -- journal chain -------------------------------------------------------------


def test_journal_chain_passes_on_an_untouched_chain(tmp_path):
    path = journal_at(tmp_path / "j.jsonl", [("a", "STARTUP", {"equity": "100000"})] * 3)
    result = V.check_journal_chain(path)
    assert result.status == V.PASS
    assert result.evidence["entries"] == 3


def test_journal_chain_fails_on_a_single_edited_byte(tmp_path):
    path = journal_at(
        tmp_path / "j.jsonl",
        [("a", "STARTUP", {"equity": "100000"}), ("a", "HEARTBEAT", {"equity": "101000"})],
    )
    lines = path.read_text().splitlines()
    record = json.loads(lines[0])
    record["payload"]["equity"] = "999999"
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")

    assert V.check_journal_chain(path).status == V.FAIL


def test_a_missing_journal_is_skipped_not_failed(tmp_path):
    result = V.check_journal_chain(tmp_path / "absent.jsonl")
    assert result.status == V.SKIP
    assert result.ok is True, "an absent artifact must not read as a contradiction"


# -- the central claim ---------------------------------------------------------


def test_selection_check_passes_when_every_choice_was_offered(tmp_path):
    path = journal_at(
        tmp_path / "j.jsonl",
        [
            ("s", "CANDIDATE_SET_BUILT", {"candidate_ids": ["gbp-1", "gbp-2"]}),
            ("t", "CANDIDATE_SELECTED", {"candidate_id": "gbp-2"}),
        ],
    )
    result = V.check_selection_was_offered(path)
    assert result.status == V.PASS
    assert result.evidence["selections"] == 1


def test_selection_check_fails_when_the_ai_named_something_never_offered(tmp_path):
    """If this ever fails on real evidence, the AI authored a trade."""
    path = journal_at(
        tmp_path / "j.jsonl",
        [
            ("s", "CANDIDATE_SET_BUILT", {"candidate_ids": ["gbp-1"]}),
            ("t", "CANDIDATE_SELECTED", {"candidate_id": "gbp-invented"}),
        ],
    )
    result = V.check_selection_was_offered(path)
    assert result.status == V.FAIL
    assert "gbp-invented" in result.evidence["unoffered"]


def test_selection_check_skips_when_nothing_was_selected_yet(tmp_path):
    path = journal_at(tmp_path / "j.jsonl", [("s", "CANDIDATE_SET_BUILT", {"candidate_ids": []})])
    assert V.check_selection_was_offered(path).status == V.SKIP


def test_model_output_check_fails_on_an_executable_field(tmp_path):
    path = journal_at(
        tmp_path / "j.jsonl",
        [("t", "CANDIDATE_SELECTED", {"model_output": {"candidate_id": "x", "qty": 50}})],
    )
    result = V.check_no_unbounded_ai_fields(path)
    assert result.status == V.FAIL
    assert "qty" in result.evidence["fields"][0]


def test_model_output_check_passes_on_an_id_only_response(tmp_path):
    path = journal_at(
        tmp_path / "j.jsonl",
        [("t", "CANDIDATE_SELECTED", {"model_output": {"candidate_id": "gbp-1"}})],
    )
    assert V.check_no_unbounded_ai_fields(path).status == V.PASS


# -- release manifest ----------------------------------------------------------


def _manifest_file(tmp_path) -> Path:
    from glassbox.release import ReleaseManifest

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
    path = tmp_path / "release.json"
    manifest.write(path, environment={})
    return path


def test_release_manifest_check_passes_on_a_real_manifest(tmp_path):
    assert V.check_release_manifest(_manifest_file(tmp_path)).status == V.PASS


def test_release_manifest_check_fails_when_it_was_edited_after_build(tmp_path):
    path = _manifest_file(tmp_path)
    raw = json.loads(path.read_text())
    raw["expected_account_suffix"] = "...0000"
    path.write_text(json.dumps(raw))

    assert V.check_release_manifest(path).status == V.FAIL


# -- position ledger -----------------------------------------------------------


def test_ledger_check_reports_what_is_held(tmp_path):
    book = PositionLedger(account_id="PA-1", environment="scored")
    book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(4),
        side="buy",
    )
    path = tmp_path / "ledger.json"
    book.save(path)

    result = V.check_position_ledger(path, account_id="PA-1", environment="scored")
    assert result.status == V.PASS
    assert result.evidence["held"] == {CALL: "4"}


def test_ledger_check_fails_on_a_tampered_ledger(tmp_path):
    book = PositionLedger(account_id="PA-1", environment="scored")
    book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(4),
        side="buy",
    )
    path = tmp_path / "ledger.json"
    book.save(path)
    raw = json.loads(path.read_text())
    raw["entries"][0]["signed_qty"] = "999"
    path.write_text(json.dumps(raw))

    assert V.check_position_ledger(path, account_id="PA-1", environment="scored").status == V.FAIL


# -- repository hygiene --------------------------------------------------------


def test_secret_check_finds_a_key_shaped_string(tmp_path):
    (tmp_path / "leak.py").write_text('KEY = "PK' + "A" * 20 + '"')
    result = V.check_no_secrets_committed(tmp_path, ["leak.py"])
    assert result.status == V.FAIL
    assert "leak.py" in result.evidence["files"]


def test_secret_check_passes_on_a_clean_tree(tmp_path):
    (tmp_path / "fine.py").write_text('VALUE = "nothing to see"')
    assert V.check_no_secrets_committed(tmp_path, ["fine.py"]).status == V.PASS


def test_dependency_check_fails_on_an_unpinned_requirement(tmp_path):
    (tmp_path / "requirements.lock").write_text("alpaca-py>=0.40\n")
    (tmp_path / "requirements-dev.lock").write_text("pytest==9.1.1\n")
    result = V.check_dependency_locks(tmp_path)
    assert result.status == V.FAIL
    assert "unpinned" in result.detail


def test_dependency_check_passes_on_this_repository():
    assert V.check_dependency_locks(Path(__file__).resolve().parents[1]).status == V.PASS


def test_proof_bundle_marked_incomplete_is_skipped_not_passed(tmp_path):
    path = tmp_path / "cli_proof.json"
    path.write_text(json.dumps({"complete": False}))
    assert V.check_proof_bundle(path, "CLI").status == V.SKIP

    path.write_text(json.dumps({"complete": True}))
    assert V.check_proof_bundle(path, "CLI").status == V.PASS


# -- the report ----------------------------------------------------------------


def _with_locks(tmp_path) -> Path:
    """A missing lock is a genuine failure, so a fixture exercising the rest
    of the report has to supply real ones."""
    (tmp_path / "requirements.lock").write_text("alpaca-py==0.44.0\n")
    (tmp_path / "requirements-dev.lock").write_text("pytest==9.1.1\n")
    return tmp_path


def test_report_is_ok_when_nothing_contradicts_even_if_much_is_absent(tmp_path):
    _with_locks(tmp_path)
    report = V.run_all(
        tmp_path,
        journal_path=tmp_path / "none.jsonl",
        manifest_path=tmp_path / "none.json",
        ledger_path=tmp_path / "none-ledger.json",
        tracked=[],
    )
    assert report.ok is True
    assert report.failed == 0
    assert report.skipped > 0


def test_one_failure_makes_the_whole_report_not_ok(tmp_path):
    _with_locks(tmp_path)
    (tmp_path / "leak.py").write_text('K = "PK' + "B" * 20 + '"')
    report = V.run_all(
        tmp_path,
        journal_path=tmp_path / "none.jsonl",
        manifest_path=tmp_path / "none.json",
        ledger_path=tmp_path / "none-ledger.json",
        tracked=["leak.py"],
    )
    assert report.ok is False
    assert report.failed == 1


def test_report_serialises_every_check(tmp_path):
    _with_locks(tmp_path)
    report = V.run_all(
        tmp_path,
        journal_path=tmp_path / "none.jsonl",
        manifest_path=tmp_path / "none.json",
        ledger_path=tmp_path / "none-ledger.json",
        tracked=[],
    )
    payload = report.as_dict()
    assert payload["checks"], "an empty report proves nothing"
    assert all({"name", "status", "detail"} <= set(c) for c in payload["checks"])


def test_the_verifier_runs_against_this_repository():
    """The tool a judge runs must work on the real tree, not only fixtures."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "tools/verify_submission.py", "--json", "--skip-claims"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["failed"] == 0, payload
    assert result.returncode == 0


# -- deterministic replay ------------------------------------------------------


def _replayable_journal(tmp_path, tamper=False, unoffered=False):
    """A journal holding one genuinely-addressed candidate set."""
    import tests.test_candidates as fixtures
    from glassbox.candidates import CANDIDATE_SCHEMA_VERSION, build_candidate_manifest

    manifest = build_candidate_manifest([fixtures._candidate("SPY"), fixtures._candidate("QQQ")])
    entries = [
        {"candidate_id": e.candidate_id, "content_hash": e.content_hash}
        for e in manifest.candidates
    ]
    if tamper:
        entries[0]["content_hash"] = "tampered"

    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.append(
        "scheduler",
        "CANDIDATE_SET_BUILT",
        {
            "manifest_hash": manifest.manifest_hash,
            "manifest_entries": entries,
            "manifest_schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_ids": list(manifest.candidate_ids),
            "manifest_unavailable": None,
        },
    )
    chosen = "gbp-never-offered" if unoffered else manifest.candidate_ids[0]
    j.append("thesis", "CANDIDATE_SELECTED", {"candidate_id": chosen})
    return path


def test_replay_check_passes_on_a_genuine_recorded_set(tmp_path):
    result = V.check_candidate_replay(_replayable_journal(tmp_path))
    assert result.status == V.PASS
    assert result.evidence["sets_verified"] == 1


def test_replay_check_fails_when_the_recorded_parts_were_edited(tmp_path):
    """The published hash no longer follows from what was recorded."""
    result = V.check_candidate_replay(_replayable_journal(tmp_path, tamper=True))
    assert result.status == V.FAIL
    assert "rebuild" in result.detail


def test_replay_check_fails_on_a_selection_that_was_never_offered(tmp_path):
    result = V.check_candidate_replay(_replayable_journal(tmp_path, unoffered=True))
    assert result.status == V.FAIL
    assert "never offered" in result.detail


def test_replay_check_skips_when_nothing_has_been_offered(tmp_path):
    path = tmp_path / "j.jsonl"
    Journal(path).append("s", "STARTUP", {"equity": "100000"})
    assert V.check_candidate_replay(path).status == V.SKIP
