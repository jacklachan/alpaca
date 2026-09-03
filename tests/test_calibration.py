"""The calibration report must stay runnable and stay honest.

Its whole value is that a reader can check a forecast the system published
before the outcome existed. If it ever needs credentials to show the forecast,
or stops stating what it does not prove, it becomes another claim.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_journal(tmp_path: Path) -> Path:
    """A journal carrying one pre-trade forecast."""
    path = tmp_path / "journal.jsonl"
    record = {
        "seq": 1,
        "ts": "2026-09-01T14:28:25Z",
        "actor": "risk.kernel",
        "event": "PLAN_APPROVED",
        "payload": {
            "evidence": [
                "surface: decay to measurement 0.2992 of premium over 2.23d",
                "premium_at_risk=17780",
            ]
        },
        "prev_hash": "0" * 64,
        "hash": "a" * 64,
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def _run(journal: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALPACA_")}
    env["GLASSBOX_JOURNAL_PATH"] = str(journal)
    return subprocess.run(
        [sys.executable, "tools/calibration.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def test_the_forecast_is_readable_without_credentials(tmp_path):
    """The forecast lives in the journal. Reading it must never need a broker."""
    result = _run(_write_journal(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_it_states_what_it_does_not_prove():
    """A calibration result is easy to overread as 'the trade was good'."""
    source = (ROOT / "tools" / "calibration.py").read_text(encoding="utf-8")
    assert "does NOT show the trade was good" in source
    assert "Direction is the other half" in source


def test_it_points_at_the_chain_rather_than_asserting_provenance():
    """The claim is only worth anything because the entry predates the outcome
    and the chain can be checked. Say so, and name the tool."""
    source = (ROOT / "tools" / "calibration.py").read_text(encoding="utf-8")
    assert "verify_chain" in source
    assert "before each order" in source
