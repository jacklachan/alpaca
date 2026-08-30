"""Release identity: what ran, against which account, built from what source.

Everything here fails closed toward "we cannot prove this release is the one we
reviewed", because the alternative is evidence attributed to an unidentified
program.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from glassbox import release as R
from glassbox.release import ReleaseError, ReleaseManifest

ROOT = Path(__file__).resolve().parents[1]

POLICY = {"max_loss": "2000", "underlyings": ["SPY", "QQQ"]}
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
REQUIRED_CHECKS = (
    "journal_chain",
    "account_identity",
    "cli_proof",
    "development_venue_proof",
    "deployment_soak",
)

# Assembled at runtime so no key-shaped literal is ever committed. CI scans
# tracked files for PK-shaped strings; a fixture that trips that scan would
# push someone toward loosening a gate whose whole job is catching a real
# key. The joined value is still PK-shaped, so it exercises redaction.
FAKE_ALPACA_KEY = "PK" + "TESTONLYNOTAREALKEY01"


def manifest(**overrides) -> ReleaseManifest:
    verification_supplied = "verification" in overrides
    supplied_verification = overrides.pop("verification", None)
    base = ReleaseManifest(
        commit="a" * 40,
        dirty=False,
        python_version="3.12.7",
        platform="test",
        runtime_lock_sha256="r" * 64,
        dev_lock_sha256="d" * 64,
        config_policy_hash=R.config_policy_hash(POLICY),
        resolved_endpoint="https://paper-api.alpaca.markets",
        environment="scored",
        expected_account_suffix="...9012",
        strategy_allowlist=("event_vol",),
        option_underlyings=("SPY", "QQQ"),
        candidate_schema_version=1,
        built_at="2026-08-31T11:30:00Z",
    )
    built = replace(base, **overrides)
    verification = (
        supplied_verification
        if verification_supplied
        else {
            "status": "RELEASE VERIFIED",
            "verified_at": "2026-08-31T11:30:00Z",
            "commit": built.commit,
            "runtime_lock_sha256": built.runtime_lock_sha256,
            "dev_lock_sha256": built.dev_lock_sha256,
            "config_policy_hash": built.config_policy_hash,
            "expected_account_suffix": built.expected_account_suffix,
            "resolved_endpoint": "https://paper-api.alpaca.markets",
            "candidate_schema_version": built.candidate_schema_version,
            "checks": {name: "PASS" for name in REQUIRED_CHECKS},
            "artifact_sha256": {name: "e" * 64 for name in REQUIRED_CHECKS},
        }
    )
    return replace(built, verification=verification or {})


# -- account and venue binding -------------------------------------------------


def test_manifest_refuses_live_endpoint_or_missing_expected_account():
    with pytest.raises(ReleaseError, match="paper endpoint"):
        manifest(resolved_endpoint="https://api.alpaca.markets").validate()
    with pytest.raises(ReleaseError, match="expected account"):
        manifest(expected_account_suffix="").validate()


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://paper-api.alpaca.markets.evil.example",
        "https://evil.example/paper-api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets/v2",
        "https://user@paper-api.alpaca.markets",
    ),
)
def test_manifest_requires_the_exact_normalized_paper_endpoint(endpoint):
    with pytest.raises(ReleaseError, match="paper endpoint"):
        manifest(resolved_endpoint=endpoint).validate()

    trailing_slash = manifest(resolved_endpoint="https://paper-api.alpaca.markets/")
    trailing_slash.validate()


def test_manifest_requires_a_full_commit_sha():
    with pytest.raises(ReleaseError, match="full sha"):
        manifest(commit="abc123").validate()


def test_account_id_is_reduced_to_a_suffix():
    assert R.redact_account("PA3XYZ789012") == "...9012"
    assert R.redact_account("") == ""
    assert R.redact_account("abc") == "..."


# -- the scored start gate -----------------------------------------------------


def test_scored_start_refuses_dirty_or_wrong_commit_when_release_gate_enabled():
    manifest().assert_scored_startable(approved_commit="a" * 40, now=NOW)

    with pytest.raises(ReleaseError, match="dirty"):
        manifest(dirty=True).assert_scored_startable(approved_commit="a" * 40, now=NOW)

    with pytest.raises(ReleaseError, match="not 'scored'"):
        manifest(environment="dev").assert_scored_startable(approved_commit="a" * 40, now=NOW)

    with pytest.raises(ReleaseError, match="approved commit"):
        manifest().assert_scored_startable(approved_commit="", now=NOW)
    with pytest.raises(ReleaseError, match="approved commit"):
        manifest().assert_scored_startable(approved_commit="b" * 40, now=NOW)


def test_scored_start_refuses_pending_or_incomplete_release_evidence():
    with pytest.raises(ReleaseError, match="pending release gates"):
        manifest(pending_gates=("deployment soak",)).assert_scored_startable(
            approved_commit="a" * 40, now=NOW
        )

    with pytest.raises(ReleaseError, match="verification evidence"):
        manifest(verification={}).assert_scored_startable(approved_commit="a" * 40, now=NOW)

    skipped = manifest()
    skipped.verification["checks"]["cli_proof"] = "SKIP"
    with pytest.raises(ReleaseError, match="cli_proof"):
        skipped.assert_scored_startable(approved_commit="a" * 40, now=NOW)

    no_hash = manifest()
    del no_hash.verification["artifact_sha256"]["deployment_soak"]
    with pytest.raises(ReleaseError, match="deployment_soak"):
        no_hash.assert_scored_startable(approved_commit="a" * 40, now=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("commit", "b" * 40),
        ("runtime_lock_sha256", "x" * 64),
        ("dev_lock_sha256", "y" * 64),
        ("config_policy_hash", "z" * 64),
        ("expected_account_suffix", "...0000"),
        ("candidate_schema_version", 99),
    ),
)
def test_scored_start_refuses_evidence_bound_to_another_release(field, value):
    candidate = manifest()
    candidate.verification[field] = value
    with pytest.raises(ReleaseError, match=field):
        candidate.assert_scored_startable(approved_commit="a" * 40, now=NOW)


def test_scored_start_refuses_stale_or_future_dated_evidence():
    stale = manifest()
    stale.verification["verified_at"] = "2026-08-30T11:59:59Z"
    with pytest.raises(ReleaseError, match="stale"):
        stale.assert_scored_startable(approved_commit="a" * 40, now=NOW)

    future = manifest()
    future.verification["verified_at"] = "2026-08-31T12:06:00Z"
    with pytest.raises(ReleaseError, match="future"):
        future.assert_scored_startable(approved_commit="a" * 40, now=NOW)


def test_approved_manifest_must_match_the_current_checkout(tmp_path):
    path = tmp_path / "release.json"
    manifest().write(path, environment={})

    loaded = R.load_approved(
        path,
        current=manifest(),
        approved_commit="a" * 40,
        now=NOW,
    )
    assert loaded.commit == "a" * 40

    with pytest.raises(ReleaseError, match="current checkout drift"):
        R.load_approved(
            path,
            current=manifest(runtime_lock_sha256="x" * 64),
            approved_commit="a" * 40,
            now=NOW,
        )


def test_approved_manifest_compares_the_endpoint_after_normalization(tmp_path):
    path = tmp_path / "release.json"
    manifest(resolved_endpoint="https://paper-api.alpaca.markets/").write(path, environment={})

    loaded = R.load_approved(
        path,
        current=manifest(resolved_endpoint="https://paper-api.alpaca.markets"),
        approved_commit="a" * 40,
        now=NOW,
    )
    assert loaded.resolved_endpoint.endswith("/")


def test_manifest_binds_options_only_strategy_allowlist():
    with pytest.raises(ReleaseError, match="options-only"):
        manifest(strategy_allowlist=("event_vol", "core")).assert_scored_startable(
            approved_commit="a" * 40, now=NOW
        )
    with pytest.raises(ReleaseError, match="options-only"):
        manifest(strategy_allowlist=("event_vol", "crypto")).assert_scored_startable(
            approved_commit="a" * 40, now=NOW
        )
    with pytest.raises(ReleaseError, match="empty strategy allowlist"):
        manifest(strategy_allowlist=()).assert_scored_startable(approved_commit="a" * 40, now=NOW)


# -- secrets -------------------------------------------------------------------


def test_manifest_never_contains_credentials_or_webhook(tmp_path):
    env = {
        "ALPACA_API_KEY": FAKE_ALPACA_KEY,
        "ALPACA_SECRET_KEY": "secretsecretsecret123456",
        "ANTHROPIC_API_KEY": "sk-ant-abcdefghijklmnop",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc",
    }
    path = tmp_path / "release.json"
    payload = manifest().write(path, environment=env)

    body = json.dumps(payload)
    for value in env.values():
        assert value not in body
    assert "sk-ant-" not in body.lower()
    assert "discord.com/api/webhooks" not in body.lower()


def test_a_leaked_credential_value_is_caught_before_the_file_is_written(tmp_path):
    env = {"ALPACA_SECRET_KEY": "secretsecretsecret123456"}
    leaky = manifest(platform="host secretsecretsecret123456")
    path = tmp_path / "release.json"

    with pytest.raises(ReleaseError, match="ALPACA_SECRET_KEY"):
        leaky.write(path, environment=env)
    assert not path.exists(), "the manifest was written before it was checked"


def test_a_credential_marker_is_caught_even_from_an_unknown_variable(tmp_path):
    leaky = manifest(built_at="sk-ant-leakedfromsomewhere")
    with pytest.raises(ReleaseError, match="credential marker"):
        leaky.write(tmp_path / "release.json", environment={})


# -- drift ---------------------------------------------------------------------


def test_manifest_detects_lock_config_or_binary_drift():
    baseline = manifest()
    assert baseline.detect_drift(manifest()) == ()

    assert "commit" in baseline.detect_drift(manifest(commit="b" * 40))
    assert "runtime_lock_sha256" in baseline.detect_drift(manifest(runtime_lock_sha256="x" * 64))
    assert "config_policy_hash" in baseline.detect_drift(
        manifest(config_policy_hash=R.config_policy_hash({"max_loss": "9999"}))
    )
    assert "strategy_allowlist" in baseline.detect_drift(
        manifest(strategy_allowlist=("event_vol", "core"))
    )
    assert "expected_account_suffix" in baseline.detect_drift(
        manifest(expected_account_suffix="...0000")
    )


# -- round trip ----------------------------------------------------------------


def test_manifest_round_trips_and_rejects_post_hoc_edits(tmp_path):
    path = tmp_path / "release.json"
    manifest().write(path, environment={})

    restored = ReleaseManifest.from_json(json.loads(path.read_text()))
    assert restored.detect_drift(manifest()) == ()

    tampered = json.loads(path.read_text())
    tampered["expected_account_suffix"] = "...0000"
    path.write_text(json.dumps(tampered))

    with pytest.raises(ReleaseError, match="hash mismatch"):
        ReleaseManifest.from_json(json.loads(path.read_text()))


def test_schema_version_mismatch_is_refused():
    raw = manifest().to_json()
    raw["schema_version"] = 99
    with pytest.raises(ReleaseError, match="schema"):
        ReleaseManifest.from_json(raw)


# -- built from the real tree --------------------------------------------------


def test_build_describes_this_checkout():
    built = R.build(
        root=ROOT,
        environment="dev",
        resolved_endpoint="https://paper-api.alpaca.markets",
        expected_account_id="PA3XYZ789012",
        strategy_allowlist=("event_vol",),
        option_underlyings=("SPY", "QQQ"),
        candidate_schema_version=1,
        policy=POLICY,
        built_at="2026-09-03T18:00:00Z",
        pending_gates=("development venue proof",),
    )
    assert len(built.commit) == 40
    assert built.runtime_lock_sha256 == R.file_sha256(ROOT / "requirements.lock")
    assert built.expected_account_suffix == "...9012"
    built.validate()


# -- the gate as main.py actually builds it ------------------------------------


def test_main_builds_an_options_only_manifest_for_the_scored_account(monkeypatch):
    """The manifest allowlist and the agent's strategy surface must come from
    one source, or the manifest can certify something the agent does not do."""
    import main

    monkeypatch.setenv("ALPACA_ENV", "scored")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "PA3XYZ789012")

    built = main.release_manifest()

    assert built.environment == "scored"
    assert built.strategy_allowlist == ("event_vol",)
    assert built.expected_account_suffix == "...9012"
    built.validate()

    # The scored gate may still refuse a dirty tree; what it must never refuse
    # is this allowlist.
    try:
        built.assert_scored_startable()
    except ReleaseError as exc:
        assert "options-only" not in str(exc), exc


def test_main_manifest_names_every_dev_strategy(monkeypatch):
    import main

    monkeypatch.setenv("ALPACA_ENV", "dev")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", "PA-DEV-0001")

    built = main.release_manifest()

    assert set(built.strategy_allowlist) == {"core", "crypto", "event_vol"}
    # A dev manifest must never pass the scored gate.
    with pytest.raises(ReleaseError):
        built.assert_scored_startable()


def test_strategy_names_matches_the_constructed_surface():
    import main

    for environment in ("dev", "scored"):
        names = set(main.strategy_names(environment))
        built = {
            n.rsplit("_", 1)[0] if n.startswith("event_vol") else n
            for n in main.strategy_set(environment, data=None)
        }
        assert built == names, f"{environment}: manifest and agent disagree"
