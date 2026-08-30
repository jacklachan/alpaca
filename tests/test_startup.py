"""Startup authority is established before credentials or broker mutation."""

from __future__ import annotations

import sys

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
