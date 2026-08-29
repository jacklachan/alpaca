"""Regression tests for crash recovery of the journal.

The bug these lock down: append() is write + flush + fsync, but a SIGKILL can
land between the write and the fsync and leave a partial line. _recover() used
to json.loads() that line unconditionally, so one unlucky kill made the process
permanently unstartable -- the agent that was supposed to run four days
unattended would be dead until a human hand-edited the file.

A torn line is by definition the entry we were part-way through writing, so it
was never acknowledged and nothing depends on it. Discarding it is safe.
Discarding anything more than it is not, and must still refuse.
"""

from __future__ import annotations

import json

import pytest

from glassbox.journal import GENESIS, Journal


def _seed(path, n=5):
    j = Journal(path)
    for i in range(n):
        j.append("test", "TICK", {"i": i})
    return j


class TestTornLine:
    def test_partial_final_line_does_not_prevent_startup(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        j = _seed(p)
        seq_before, head_before = j.seq, j.head

        with p.open("a") as fh:
            fh.write('{"seq": 99, "ts": "2026-09-01T00:00:00Z", "act')

        recovered = Journal(p)                 # must not raise
        ok, why = recovered.verify()
        assert ok, why
        # +1 for the TORN_ENTRY_DISCARDED marker the recovery writes.
        assert recovered.seq == seq_before + 1

    def test_intact_prefix_survives_untouched(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        j = _seed(p)
        seq_before, head_before = j.seq, j.head
        with p.open("a") as fh:
            fh.write('{"seq": 99, "partial')

        records = list(Journal(p).read())
        assert any(r["seq"] == seq_before and r["hash"] == head_before
                   for r in records)

    def test_recovery_is_recorded_in_the_chain(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        _seed(p)
        with p.open("a") as fh:
            fh.write("{oops")

        events = [r["event"] for r in Journal(p).read()]
        assert "TORN_ENTRY_DISCARDED" in events

    def test_chain_continues_correctly_after_recovery(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        _seed(p)
        with p.open("a") as fh:
            fh.write("{oops")

        j = Journal(p)
        j.append("test", "AFTER", {"ok": True})
        ok, why = Journal(p).verify()
        assert ok, why

    def test_only_entry_torn_starts_a_fresh_chain(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        p.write_text('{"seq": 1, "tr')
        j = Journal(p)
        ok, why = j.verify()
        assert ok, why

    def test_empty_and_missing_files(self, tmp_path):
        missing = Journal(tmp_path / "nope.jsonl")
        assert missing.seq == 0 and missing.head == GENESIS
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        j = Journal(empty)
        assert j.seq == 0 and j.head == GENESIS


class TestTamperDetection:
    def test_edited_payload_is_detected(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        _seed(p, 6)
        lines = p.read_text().splitlines()
        rec = json.loads(lines[2])
        rec["payload"]["i"] = 999
        lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        p.write_text("\n".join(lines) + "\n")

        ok, why = Journal(p).verify()
        assert not ok
        assert "hash mismatch" in why

    def test_every_position_is_tamper_evident(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        _seed(p, 6)
        original = p.read_text().splitlines()

        for idx in range(len(original)):
            lines = list(original)
            rec = json.loads(lines[idx])
            rec["actor"] = "tampered"
            lines[idx] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            target = tmp_path / f"j{idx}.jsonl"
            target.write_text("\n".join(lines) + "\n")
            ok, _ = Journal(target).verify()
            assert not ok, f"tamper at index {idx} went undetected"

    def test_deleted_entry_is_detected(self, tmp_path):
        p = tmp_path / "journal.jsonl"
        _seed(p, 6)
        lines = p.read_text().splitlines()
        del lines[3]
        p.write_text("\n".join(lines) + "\n")
        ok, why = Journal(p).verify()
        assert not ok
        assert "seq gap" in why or "chain break" in why

    def test_verify_reports_rather_than_raises_on_bad_json(self, tmp_path):
        """verify() runs on the startup path and in the demo verifier.

        Both need a verdict they can print, not a traceback. Damage in the
        middle of the file is not touched by the torn-line recovery (that only
        looks at the final line), so verify() must handle it gracefully.
        """
        p = tmp_path / "journal.jsonl"
        _seed(p, 3)
        lines = p.read_text().splitlines()
        lines.insert(1, "{not json at all")
        p.write_text("\n".join(lines) + "\n")

        ok, why = Journal(p).verify()
        assert not ok
        assert "unparseable" in why
