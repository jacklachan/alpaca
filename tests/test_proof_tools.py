"""The CLI evidence capture tool.

Its whole value depends on one property: it cannot place an order. So the
allowlist, the refusals, and the redaction are the tests that matter, not the
happy path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import capture_alpaca_proof as proof  # noqa: E402

NOW = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
ACCOUNT = "PA3XYZ789012"

PAPER_ENV = {
    "ALPACA_ENV": "scored",
    "ALPACA_PAPER_TRADE": "true",
    "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
    "ALPACA_API_KEY": "PKTESTKEY0123456789",
    "ALPACA_SECRET_KEY": "secretvalue0123456789",
}


def completed(stdout: str = "{}", code: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def runner_for(mapping: dict[str, object]):
    def run(argv):
        key = " ".join(argv[1:])
        result = mapping.get(key, completed())
        return result if isinstance(result, subprocess.CompletedProcess) else completed(str(result))

    return run


# -- the allowlist -------------------------------------------------------------


def test_command_builder_contains_only_allowlisted_read_operations():
    for name in proof.READ_ONLY_COMMANDS:
        argv = proof.build_argv("alpaca", name)
        assert argv[0] == "alpaca"
        assert argv[1:] == proof.READ_ONLY_COMMANDS[name]


@pytest.mark.parametrize(
    "name",
    ["order_place", "cancel", "close_position", "exercise", "account_update", "anything"],
)
def test_any_mutating_command_is_refused_before_a_process_starts(name):
    with pytest.raises(proof.ProofRefused, match="allowlisted"):
        proof.build_argv("alpaca", name)


def test_a_mutating_token_smuggled_into_the_allowlist_is_still_refused(monkeypatch):
    """Defence in depth: if the table itself gains a dangerous entry, the token
    scan must still stop it."""
    monkeypatch.setitem(proof.READ_ONLY_COMMANDS, "sneaky", ("order", "cancel", "--all"))
    with pytest.raises(proof.ProofRefused, match="mutating token"):
        proof.build_argv("alpaca", "sneaky")


# -- profile and credential gates ----------------------------------------------


def test_non_paper_profile_fails_closed():
    with pytest.raises(proof.ProofRefused, match="not true"):
        proof.assert_paper_profile({**PAPER_ENV, "ALPACA_PAPER_TRADE": "false"})
    with pytest.raises(proof.ProofRefused, match="paper endpoint"):
        proof.assert_paper_profile({**PAPER_ENV, "ALPACA_BASE_URL": "https://api.alpaca.markets"})


def test_partial_credential_bundle_fails_closed():
    partial = {k: v for k, v in PAPER_ENV.items() if k != "ALPACA_SECRET_KEY"}
    with pytest.raises(proof.ProofRefused, match="ALPACA_SECRET_KEY"):
        proof.capture(
            binary="alpaca",
            names=["version"],
            environment=partial,
            expected_account_id=ACCOUNT,
            runner=runner_for({}),
            now=NOW,
        )


# -- account identity ----------------------------------------------------------


def test_returned_account_id_must_equal_the_expected_id():
    matching = runner_for({"account get": completed(json.dumps({"account_number": ACCOUNT}))})
    bundle = proof.capture(
        binary="alpaca",
        names=["account"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=matching,
        now=NOW,
    )
    assert bundle.account_id_matches_expected is True
    assert bundle.complete is True

    wrong = runner_for(
        {"account get": completed(json.dumps({"account_number": "PA-SOMEONE-ELSE"}))}
    )
    other = proof.capture(
        binary="alpaca",
        names=["account"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=wrong,
        now=NOW,
    )
    assert other.account_id_matches_expected is False
    assert other.complete is False, "a wrong account must not read as complete proof"


# -- incompleteness ------------------------------------------------------------


def test_nonzero_exit_marks_the_proof_incomplete():
    bundle = proof.capture(
        binary="alpaca",
        names=["clock"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=runner_for({"clock get": completed("", code=1, stderr="boom")}),
        now=NOW,
    )
    assert bundle.steps[0].complete is False
    assert bundle.steps[0].exit_code == 1
    assert bundle.complete is False


def test_invalid_json_marks_the_proof_incomplete():
    bundle = proof.capture(
        binary="alpaca",
        names=["clock"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=runner_for({"clock get": completed("not json at all")}),
        now=NOW,
    )
    assert bundle.steps[0].complete is False
    assert bundle.complete is False


# -- redaction and storage -----------------------------------------------------


def test_raw_output_is_redacted_hashed_and_timestamped(tmp_path):
    leaky = json.dumps(
        {
            "account_number": ACCOUNT,
            "key": PAPER_ENV["ALPACA_API_KEY"],
            "secret": PAPER_ENV["ALPACA_SECRET_KEY"],
            "note": "sk-ant-abcdefghijklmnop",
            "hook": "https://discord.com/api/webhooks/1/abc",
        }
    )
    bundle = proof.capture(
        binary="alpaca",
        names=["account"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=runner_for({"account get": completed(leaky)}),
        now=NOW,
    )
    step = bundle.steps[0]

    assert PAPER_ENV["ALPACA_API_KEY"] not in step.stdout_redacted
    assert PAPER_ENV["ALPACA_SECRET_KEY"] not in step.stdout_redacted
    assert "sk-ant-abcdefghijklmnop" not in step.stdout_redacted
    assert "discord.com/api/webhooks/1/abc" not in step.stdout_redacted
    # The hash is of the real output, so it still cites what was actually seen.
    assert step.stdout_sha256 == proof._sha256(leaky)
    assert step.captured_at == NOW.isoformat()

    path = tmp_path / "proof.json"
    payload = proof.write_bundle(bundle, path)
    stored = json.loads(path.read_text())
    assert stored == payload
    body = path.read_text()
    for secret in (PAPER_ENV["ALPACA_API_KEY"], PAPER_ENV["ALPACA_SECRET_KEY"]):
        assert secret not in body


def test_stderr_is_redacted_too():
    bundle = proof.capture(
        binary="alpaca",
        names=["clock"],
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=runner_for(
            {
                "clock get": completed(
                    "", code=1, stderr=f"auth failed for {PAPER_ENV['ALPACA_API_KEY']}"
                )
            }
        ),
        now=NOW,
    )
    assert PAPER_ENV["ALPACA_API_KEY"] not in bundle.steps[0].stderr_redacted


def test_a_full_read_only_sweep_builds_every_step():
    bundle = proof.capture(
        binary="alpaca",
        names=list(proof.READ_ONLY_COMMANDS),
        environment=PAPER_ENV,
        expected_account_id=ACCOUNT,
        runner=runner_for({"account get": completed(json.dumps({"account_number": ACCOUNT}))}),
        now=NOW,
    )
    assert len(bundle.steps) == len(proof.READ_ONLY_COMMANDS)
    assert all(
        not any(t in " ".join(s.argv).lower() for t in ("cancel", "place", "close", "exercise"))
        for s in bundle.steps
    )
