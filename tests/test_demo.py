"""The credential-free demo has to stay credential-free.

Its whole value is that a reader can run it on a fresh clone with no Alpaca
account and watch the shipped kernel refuse things. If it ever acquires a
credential requirement or a network call, it stops being evidence and becomes
another claim.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_without_credentials() -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
            "LLM_API_KEY",
            "FEATHERLESS_API_KEY",
            "ANTHROPIC_API_KEY",
        }
    }
    # Point .env resolution at a directory with no .env, so a developer's real
    # file cannot quietly satisfy what a judge's clone would not have.
    env["GLASSBOX_DISABLE_DOTENV"] = "1"
    return subprocess.run(
        [sys.executable, "tools/demo.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def test_the_demo_runs_with_no_credentials():
    result = _run_without_credentials()
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]


def test_the_demo_shows_every_hostile_plan_refused():
    out = _run_without_credentials().stdout
    assert "All 4 refused" in out
    for invariant in ("01_symbol_allowlist", "02_bounded_max_loss", "05_concentration"):
        assert invariant in out, f"{invariant} no longer appears in the demo"


def test_the_demo_shows_both_hold_and_flatten():
    """A demo that only ever flattens proves nothing about judgement."""
    out = _run_without_credentials().stdout
    assert "HOLD" in out
    assert "FLATTEN" in out
    assert "no two-sided quote" in out


def test_the_demo_shows_the_chain_detecting_a_tamper():
    out = _run_without_credentials().stdout
    assert "chain intact" in out
    assert "FAIL (correct)" in out


def test_the_demo_states_the_honest_limit_of_the_chain():
    """Overclaiming here would undo the point of showing it at all."""
    out = _run_without_credentials().stdout
    assert "reconcilable, not tamper-proof" in out
