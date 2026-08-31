"""The one check between the rehearsal tool and the scored account.

tools/practice.py exists so the scored account's trade history stays clean, and
its only protection is an environment comparison. That comparison has to
resolve the variable exactly the way Broker does, or the tool can refuse to run
while the Broker it builds connects to the scored account anyway.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from glassbox import env as env_module

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "raw",
    [
        "scored",
        "scored  # the scored account",
        "  scored  ",
        "scored\t# note",
    ],
)
def test_every_way_of_writing_scored_resolves_to_scored(monkeypatch, raw: str):
    """systemd's EnvironmentFile parser keeps inline '#' comments in the value.
    Each of these reaches Broker as "scored", so each must reach the guard as
    "scored" too."""
    monkeypatch.setenv("ALPACA_ENV", raw)
    assert env_module.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev") == "scored"


def test_a_commented_scored_value_is_not_equal_to_scored_by_plain_comparison(monkeypatch):
    """The bug this guards. If practice.py ever goes back to os.getenv, this
    is the value that walks straight past it."""
    import os

    monkeypatch.setenv("ALPACA_ENV", "scored  # the scored account")
    assert os.getenv("ALPACA_ENV") != "scored"
    assert env_module.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev") == "scored"


def test_practice_resolves_the_environment_the_way_the_broker_does():
    source = (ROOT / "tools" / "practice.py").read_text(encoding="utf-8")
    assert "require_choice" in source, "practice.py must resolve like the Broker"
    assert 'os.getenv("ALPACA_ENV"' not in source, "practice.py compares a raw env value"


@pytest.mark.parametrize("raw", ["scored", "scored  # the scored account"])
def test_practice_refuses_to_run_against_the_scored_account(raw: str):
    """End to end: the tool must exit non-zero and say so, for every spelling
    of scored that the Broker would accept."""
    import os

    environment = dict(os.environ)
    environment["ALPACA_ENV"] = raw
    environment.pop("ALPACA_API_KEY", None)
    environment.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "tools/practice.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSING" in result.stdout
