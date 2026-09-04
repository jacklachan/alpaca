"""The calibration report must stay runnable, stay honest, and stay still.

Its whole value is that a reader can check a forecast the system published
before the outcome existed. If it ever needs credentials to show the forecast,
stops stating what it does not prove, or reports a number that changes
depending on when you run it, it becomes another claim.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The instant the account is valued; the report must be read as of this moment
# and no later. Kept as a literal so a change to the constant fails loudly here
# rather than silently moving a published result.
MEASUREMENT_UTC = "2026-09-03T20:00:00Z"


def _forecast_record() -> dict:
    return {
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


def _reconciled(seq: int, ts: str, equity: str) -> dict:
    return {
        "seq": seq,
        "ts": ts,
        "actor": "scheduler",
        "event": "RECONCILED",
        "payload": {"equity": equity, "convex_premium_outstanding": "7840", "positions": 1},
        "prev_hash": "0" * 64,
        "hash": "b" * 64,
    }


def _write_journal(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "journal.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
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
    result = _run(_write_journal(tmp_path, [_forecast_record()]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "29.9%" in result.stdout


def test_the_outcome_is_frozen_at_measurement_and_never_looks_ahead(tmp_path):
    """The bug this guards against, which shipped once and had to be fixed.

    The first version compared the forecast against whatever happened to be
    open when you ran it, so the published result drifted -- -1.8% of premium
    while the book was a balanced strangle, -181.8% an hour later once only a
    directional winner remained. A result that moves with the clock is not
    evidence.

    So: the reading must come from at or before the measurement instant, and a
    later one must never be preferred just because it is closer to now.
    """
    journal = _write_journal(
        tmp_path,
        [
            _forecast_record(),
            _reconciled(2, "2026-09-03T19:59:00Z", "99000.00"),
            # Everything below happens after the account was valued and must
            # not reach the report, however much more flattering it is.
            _reconciled(3, "2026-09-03T20:05:00Z", "111111.11"),
            _reconciled(4, "2026-09-04T15:00:00Z", "222222.22"),
        ],
    )
    result = _run(journal)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "99,000.00" in result.stdout, result.stdout
    assert "111,111" not in result.stdout, "reported a reading taken after measurement"
    assert "222,222" not in result.stdout, "reported a reading from the next day"


def test_it_refuses_to_call_the_difference_model_error(tmp_path):
    """A mark change contains decay and direction, and cannot be split into the
    two after the fact. The forecast was about decay alone, so the report must
    not present the gap as though it scored the model."""
    source = (ROOT / "tools" / "calibration.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "direction" in lowered
    assert "not model error" in lowered


def test_it_points_at_the_chain_rather_than_asserting_provenance():
    """The claim is only worth anything because the entry predates the outcome
    and the chain can be checked. Say so, and name the tool."""
    source = (ROOT / "tools" / "calibration.py").read_text(encoding="utf-8")
    assert "verify_chain" in source
    assert "predate the outcome" in source.lower()
