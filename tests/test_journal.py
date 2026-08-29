from __future__ import annotations

import json
from decimal import Decimal

from glassbox.journal import Journal


def test_chain_verifies_when_intact(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    for i in range(5):
        j.append("test", "EVENT", {"i": i, "amount": Decimal("1.50")})
    ok, reason = j.verify()
    assert ok, reason


def test_detects_edited_payload(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.append("risk.kernel", "PLAN_REFUSED", {"plan_id": "abc", "reason": "naked short"})
    j.append("risk.kernel", "PLAN_APPROVED", {"plan_id": "def"})

    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["reason"] = "looked fine to me"
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    ok, reason = Journal(path).verify()
    assert not ok
    assert "seq 1" in reason


def test_detects_deleted_entry(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    for i in range(4):
        j.append("test", "EVENT", {"i": i})
    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    ok, reason = Journal(path).verify()
    assert not ok


def test_resumes_chain_after_restart(tmp_path):
    path = tmp_path / "j.jsonl"
    a = Journal(path)
    a.append("test", "EVENT", {"n": 1})
    head_before = a.head

    b = Journal(path)  # simulates a process restart
    assert b.seq == 1
    assert b.head == head_before
    b.append("test", "EVENT", {"n": 2})

    ok, _ = Journal(path).verify()
    assert ok


def test_decimal_and_datetime_round_trip_deterministically(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    j.append("test", "EVENT", {"px": Decimal("650.25"), "qty": Decimal("5")})
    ok, _ = j.verify()
    assert ok
