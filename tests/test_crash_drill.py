from __future__ import annotations

from tools import crash_drill


class _RunningProcess:
    def poll(self):
        return None


def test_wait_for_journal_refuses_to_kill_before_first_durable_write(tmp_path):
    path = tmp_path / "journal.jsonl"

    assert not crash_drill._wait_for_journal(path, _RunningProcess(), timeout=0.01)


def test_wait_for_journal_accepts_nonempty_journal(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("durable\n", encoding="utf-8")

    assert crash_drill._wait_for_journal(path, _RunningProcess(), timeout=0.01)
