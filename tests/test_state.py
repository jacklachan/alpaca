"""Durable safety state must survive crashes and fail closed on corruption."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from glassbox import config as C
from glassbox.scheduler import Agent


def durable_state():
    return importlib.import_module("glassbox.state")


def test_atomic_replace_failure_preserves_the_previous_valid_file(tmp_path,
                                                                  monkeypatch):
    state = durable_state()
    path = tmp_path / "safety.json"
    path.write_text('{"version": 1}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("disk refused replace")

    monkeypatch.setattr(state.os, "replace", fail_replace)

    with pytest.raises(state.StateWriteError, match="safety.json"):
        state.atomic_write_json(path, {"version": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert list(tmp_path.glob(".safety.json.*.tmp")) == []


def test_atomic_write_replaces_complete_json_and_leaves_no_temp_file(tmp_path):
    state = durable_state()
    path = tmp_path / "safety.json"

    state.atomic_write_json(path, {"keys": ["CPI", "NFP"]})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "keys": ["CPI", "NFP"]}
    assert list(tmp_path.glob(".safety.json.*.tmp")) == []


class Journal:
    def __init__(self):
        self.events = []

    def append(self, actor, event, payload):
        self.events.append((actor, event, payload))


class Broker:
    def __init__(self):
        self.reconciliations = 0

    def reconcile(self, **kwargs):
        self.reconciliations += 1
        return SimpleNamespace(market_open=True)


class Manager:
    def __init__(self):
        self.kill = SimpleNamespace(tripped=False)
        self.ticks = 0

    def tick(self, state):
        self.ticks += 1


class Strategy:
    def __init__(self):
        self.proposals = 0

    def propose_from_state(self, state, positioned):
        self.proposals += 1
        return []


def build_agent(tmp_path, monkeypatch):
    path = tmp_path / "positioned.json"
    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(path))
    broker, manager, strategy = Broker(), Manager(), Strategy()
    agent = Agent(broker, Journal(), object(), manager, {"event": strategy})
    return agent, broker, manager, strategy


def test_corrupt_positioned_state_stops_agent_construction(tmp_path, monkeypatch):
    path = tmp_path / "positioned.json"
    path.write_text('{"day": "2026-08-29", "keys": "not-a-list"}',
                    encoding="utf-8")
    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(path))

    with pytest.raises(RuntimeError, match="positioned"):
        Agent(Broker(), Journal(), object(), Manager(), {})


def test_positioned_write_fault_blocks_entries_but_keeps_management_running(
        tmp_path, monkeypatch):
    state = durable_state()
    import glassbox.scheduler as scheduler

    agent, broker, manager, strategy = build_agent(tmp_path, monkeypatch)

    def fail_write(path, value):
        raise state.StateWriteError("disk full")

    monkeypatch.setattr(scheduler, "atomic_write_json", fail_write)
    agent._guard("mark_positioned", lambda: agent._mark_positioned("CPI"))()

    agent.equity_tick()

    assert agent._state_faulted
    assert broker.reconciliations == 1
    assert manager.ticks == 1
    assert strategy.proposals == 0
