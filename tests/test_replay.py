"""Rebuilding recorded decisions from the journal alone.

The point of replay is that it can fail. These tests check it rebuilds a real
manifest exactly, and that it refuses on the three things worth catching: an
edited journal, a hash published without its inputs, and a selection naming a
candidate that was never offered.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from glassbox import replay as R
from glassbox.candidates import CANDIDATE_SCHEMA_VERSION, build_candidate_manifest


def entries(*pairs):
    return [{"candidate_id": c, "content_hash": h} for c, h in pairs]


def built(payload_entries, manifest_hash, **kw):
    payload = {
        "manifest_hash": manifest_hash,
        "manifest_entries": payload_entries,
        "manifest_schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_ids": [e["candidate_id"] for e in payload_entries],
        "manifest_unavailable": None,
    }
    payload.update(kw)
    return {"event": "CANDIDATE_SET_BUILT", "payload": payload}


def selected(candidate_id):
    return {"event": "CANDIDATE_SELECTED", "payload": {"candidate_id": candidate_id}}


# -- it rebuilds a genuine manifest --------------------------------------------


def test_replay_rebuilds_the_hash_a_real_manifest_produced():
    """The rebuilt address must equal what build_candidate_manifest computed,
    or replay is checking something other than what the agent published."""
    import tests.test_candidates as fixtures

    candidates = [fixtures._candidate("SPY"), fixtures._candidate("QQQ")]
    manifest = build_candidate_manifest(candidates)

    rebuilt = R.rebuild_manifest_hash(
        [
            {"candidate_id": e.candidate_id, "content_hash": e.content_hash}
            for e in manifest.candidates
        ]
    )
    assert rebuilt == manifest.manifest_hash


def test_the_rebuilt_address_does_not_depend_on_recorded_order():
    """Canonical ordering is what makes the address independent of the order
    strategies happened to produce candidates in."""
    pairs = entries(("gbp-b", "h2"), ("gbp-a", "h1"))
    assert R.rebuild_manifest_hash(pairs) == R.rebuild_manifest_hash(list(reversed(pairs)))


def test_changing_any_recorded_part_changes_the_address():
    base = entries(("gbp-a", "h1"), ("gbp-b", "h2"))
    assert R.rebuild_manifest_hash(base) != R.rebuild_manifest_hash(
        entries(("gbp-a", "h1"), ("gbp-b", "CHANGED"))
    )
    assert R.rebuild_manifest_hash(base) != R.rebuild_manifest_hash(entries(("gbp-a", "h1")))


# -- it refuses on the things worth catching -----------------------------------


def test_an_edited_journal_is_caught():
    """Parts and result no longer agree: either the record was edited or the
    hashing changed. Both are worth knowing."""
    good = entries(("gbp-a", "h1"))
    record = built(good, R.rebuild_manifest_hash(good))
    record["payload"]["manifest_entries"] = entries(("gbp-a", "TAMPERED"))

    result = R.replay_record(record["payload"])
    assert result.verified is True
    assert result.matches is False
    assert "does not match" in result.reason


def test_a_hash_published_without_its_inputs_is_unverifiable_not_a_pass():
    result = R.replay_record({"manifest_hash": "abc", "manifest_entries": []})
    assert result.matches is False
    assert result.verified is False
    assert "without the parts" in result.reason


def test_a_set_the_agent_could_not_address_reports_its_own_reason():
    result = R.replay_record(
        {"manifest_hash": "", "manifest_unavailable": "CandidateDataInvalid: schema"}
    )
    assert result.verified is False
    assert "schema" in result.reason


def test_a_missing_hash_is_unverifiable():
    assert R.replay_record({}).verified is False


# -- the whole journal ---------------------------------------------------------


def test_a_clean_journal_replays_and_every_selection_was_offered():
    pairs = entries(("gbp-a", "h1"), ("gbp-b", "h2"))
    report = R.replay_journal([built(pairs, R.rebuild_manifest_hash(pairs)), selected("gbp-b")])

    assert report.ok is True
    assert report.verified == 1
    assert report.selections == ["gbp-b"]
    assert report.unoffered_selections == []


def test_a_selection_that_was_never_offered_is_a_contradiction():
    """The one failure this design exists to make impossible."""
    pairs = entries(("gbp-a", "h1"))
    report = R.replay_journal(
        [built(pairs, R.rebuild_manifest_hash(pairs)), selected("gbp-invented")]
    )

    assert report.ok is False
    assert report.unoffered_selections == ["gbp-invented"]


def test_a_tampered_set_makes_the_report_not_ok():
    pairs = entries(("gbp-a", "h1"))
    record = built(pairs, "not-the-real-hash")
    report = R.replay_journal([record])

    assert report.ok is False
    assert len(report.mismatched) == 1


def test_an_unverifiable_set_alone_is_not_a_contradiction():
    """The agent may have journalled a set it could not address, and that
    already carries its own recorded reason."""
    report = R.replay_journal(
        [{"event": "CANDIDATE_SET_BUILT", "payload": {"manifest_unavailable": "no provenance"}}]
    )
    assert report.ok is True
    assert len(report.unverifiable) == 1


def test_a_selection_before_any_set_is_not_judged():
    """Nothing has been offered yet, so absence proves nothing."""
    report = R.replay_journal([selected("gbp-a")])
    assert report.ok is True
    assert report.unoffered_selections == []


def test_an_empty_journal_replays_cleanly():
    report = R.replay_journal([])
    assert report.ok is True
    assert report.as_dict()["sets_replayed"] == 0


def test_the_report_serialises_for_a_reader():
    pairs = entries(("gbp-a", "h1"))
    body = R.replay_journal([built(pairs, R.rebuild_manifest_hash(pairs))]).as_dict()
    assert body["sets_replayed"] == 1
    assert body["sets_verified"] == 1
    assert body["ok"] is True


@pytest.mark.parametrize("bad", [{"candidate_id": "a"}, {"content_hash": "h"}])
def test_a_malformed_entry_is_unreplayable_rather_than_silently_hashed(bad):
    result = R.replay_record({"manifest_hash": "abc", "manifest_entries": [bad]})
    assert result.verified is False
    assert "unreplayable" in result.reason


def test_replay_never_touches_a_venue():
    """It must be runnable by anyone holding only the journal file."""
    source = __import__("pathlib").Path(R.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "httpx", "alpaca", "urllib", "socket"):
        assert forbidden not in source, f"replay imports {forbidden}"


def test_decimal_values_are_not_required_for_replay():
    """Replay works from recorded strings alone, with no domain objects."""
    pairs = entries(("gbp-a", str(Decimal("1.00"))))
    assert R.rebuild_manifest_hash(pairs)
