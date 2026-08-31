"""Startup authority is established before credentials or broker mutation."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


class CleanJournal:
    def verify(self):
        return True, "empty journal"


@pytest.mark.parametrize("gate", (None, "0", "false", "yes"))
def test_scored_start_refuses_a_missing_or_disabled_release_gate(monkeypatch, gate):
    import main

    monkeypatch.setattr(main, "Journal", lambda _path: CleanJournal())
    monkeypatch.setattr(
        main, "build", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("build reached"))
    )
    monkeypatch.setattr(sys, "argv", ["glassbox", "--dry-run"])
    monkeypatch.setenv("ALPACA_ENV", "scored")
    if gate is None:
        monkeypatch.delenv("GLASSBOX_RELEASE_GATE", raising=False)
    else:
        monkeypatch.setenv("GLASSBOX_RELEASE_GATE", gate)

    assert main.main() == 6


def test_dev_dry_run_does_not_require_scored_release_evidence(monkeypatch):
    import main

    class Broker:
        def assert_ready(self):
            return {
                "account_number": "...0001",
                "equity": "100000",
                "env": "dev",
                "options_level": 0,
            }

    class Scheduler:
        def get_jobs(self):
            return []

    class Agent:
        manager = type("Manager", (), {"kill": type("Kill", (), {"tripped": False})()})()

        def build(self):
            return Scheduler()

    monkeypatch.setattr(main, "Journal", lambda _path: CleanJournal())
    monkeypatch.setattr(main, "build", lambda **_kwargs: (Agent(), CleanJournal(), Broker()))
    monkeypatch.setattr(sys, "argv", ["glassbox", "--dry-run"])
    monkeypatch.setenv("ALPACA_ENV", "dev")
    monkeypatch.delenv("GLASSBOX_RELEASE_GATE", raising=False)
    monkeypatch.delenv("GLASSBOX_APPROVED_COMMIT_SHA", raising=False)

    assert main.main() == 0


@pytest.mark.parametrize("mode", ("once", "scheduler"))
def test_scored_runtime_owns_lock_before_build_for_its_full_lifetime(monkeypatch, tmp_path, mode):
    import main

    events: list[str] = []
    lock_paths = []

    class Lock:
        def __init__(self, path):
            lock_paths.append(path)

        def acquire(self):
            events.append("lock acquired")
            return self

        def release(self):
            events.append("lock released")

    class Broker:
        def assert_ready(self):
            assert events == ["lock acquired", "build"]
            events.append("account ready")

    class Agent:
        def equity_tick(self):
            assert "lock released" not in events
            events.append("tick")

        def run(self):
            assert "lock released" not in events
            events.append("run")

    def build(**_kwargs):
        assert events == ["lock acquired"]
        events.append("build")
        return Agent(), CleanJournal(), Broker()

    journal_path = tmp_path / "authoritative-state" / "journal.jsonl"
    monkeypatch.setattr(main.C, "JOURNAL_PATH", str(journal_path))
    monkeypatch.setattr(main, "Journal", lambda _path: CleanJournal())
    monkeypatch.setattr(main, "approved_release_manifest", lambda: SimpleNamespace(commit="a" * 40))
    monkeypatch.setattr(main, "ProcessLock", Lock)
    monkeypatch.setattr(main, "build", build)
    monkeypatch.setenv("ALPACA_ENV", "scored")
    monkeypatch.setattr(sys, "argv", ["glassbox", "--once"] if mode == "once" else ["glassbox"])

    assert main.main() == 0
    assert lock_paths == [journal_path.parent / "scheduler.lock"]
    assert events[-1] == "lock released"
    assert events.index("lock acquired") < events.index("build")
    assert events.index("lock released") > events.index("tick" if mode == "once" else "run")


def test_second_scored_runtime_refuses_before_broker_construction(monkeypatch, tmp_path):
    import main
    from glassbox.state import ProcessLock

    journal_path = tmp_path / "authoritative-state" / "journal.jsonl"
    lock_path = journal_path.parent / "scheduler.lock"
    owner = ProcessLock(lock_path).acquire()
    build_calls = []
    try:
        monkeypatch.setattr(main.C, "JOURNAL_PATH", str(journal_path))
        monkeypatch.setattr(main, "Journal", lambda _path: CleanJournal())
        monkeypatch.setattr(
            main, "approved_release_manifest", lambda: SimpleNamespace(commit="a" * 40)
        )
        monkeypatch.setattr(main, "build", lambda **kwargs: build_calls.append(kwargs))
        monkeypatch.setenv("ALPACA_ENV", "scored")
        monkeypatch.setattr(sys, "argv", ["glassbox", "--once"])

        assert main.main() != 0
        assert build_calls == []
    finally:
        owner.release()


def test_corrupt_scored_runtime_lock_refuses_before_broker_construction(monkeypatch, tmp_path):
    import main

    journal_path = tmp_path / "authoritative-state" / "journal.jsonl"
    lock_path = journal_path.parent / "scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("not-json")
    build_calls = []

    monkeypatch.setattr(main.C, "JOURNAL_PATH", str(journal_path))
    monkeypatch.setattr(main, "Journal", lambda _path: CleanJournal())
    monkeypatch.setattr(main, "approved_release_manifest", lambda: SimpleNamespace(commit="a" * 40))
    monkeypatch.setattr(main, "build", lambda **kwargs: build_calls.append(kwargs))
    monkeypatch.setenv("ALPACA_ENV", "scored")
    monkeypatch.setattr(sys, "argv", ["glassbox", "--once"])

    assert main.main() == 7
    assert build_calls == []
