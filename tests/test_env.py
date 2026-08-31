"""Regression tests for the systemd/dotenv parser mismatch.

The bug these lock down: an inline '#' comment in .env is stripped by
python-dotenv but kept by systemd, load_dotenv() does not override what systemd
already set, and so `Broker.env` became "scored   # dev | scored" on the VPS.
That is not equal to "scored", so assert_ready() silently skipped the
scored-account equity and cleanliness checks -- the guards that exist to catch
trading the wrong account.

It passed every hand test, because by hand there is no systemd.
"""

from __future__ import annotations

import pytest

from glassbox import env


class TestClean:
    def test_strips_inline_comment(self):
        assert env.clean("dev                  # dev | scored") == "dev"

    def test_strips_whitespace(self):
        assert env.clean("  scored  ") == "scored"

    def test_preserves_quoted_value_containing_hash(self):
        assert env.clean('"a#b"') == "a#b"

    def test_leaves_hash_without_leading_space_alone(self):
        # Not a comment by either parser's rules; part of the value.
        assert env.clean("abc#def") == "abc#def"

    def test_none_and_empty(self):
        assert env.clean(None) == ""
        assert env.clean("") == ""


class TestRequireChoice:
    def test_accepts_valid(self, monkeypatch):
        monkeypatch.setenv("ALPACA_ENV", "scored")
        assert env.require_choice("ALPACA_ENV", {"dev", "scored"}) == "scored"

    def test_accepts_value_that_systemd_mangled(self, monkeypatch):
        """The exact production failure: must resolve to 'scored', not crash."""
        monkeypatch.setenv("ALPACA_ENV", "scored               # dev | scored")
        assert env.require_choice("ALPACA_ENV", {"dev", "scored"}) == "scored"

    def test_rejects_unknown_value_loudly(self, monkeypatch):
        """A typo must crash, never fall through to the dev branch."""
        monkeypatch.setenv("ALPACA_ENV", "prod")
        with pytest.raises(env.EnvError) as exc:
            env.require_choice("ALPACA_ENV", {"dev", "scored"})
        assert "prod" in str(exc.value)

    def test_default_applies_only_when_unset(self, monkeypatch):
        monkeypatch.delenv("ALPACA_ENV", raising=False)
        assert env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev") == "dev"


class TestExpectedAccountIdentity:
    def test_returns_the_explicit_id_for_the_selected_environment(self, monkeypatch):
        monkeypatch.setenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", "DEV-123")
        monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "SCORED-456")

        assert env.expected_account_id("dev") == "DEV-123"
        assert env.expected_account_id("scored") == "SCORED-456"

    def test_rejects_a_missing_expected_id(self, monkeypatch):
        monkeypatch.delenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", raising=False)
        monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "SCORED-456")

        with pytest.raises(env.EnvError, match="ALPACA_EXPECTED_DEV_ACCOUNT_ID"):
            env.expected_account_id("dev")

    def test_rejects_equal_dev_and_scored_ids(self, monkeypatch):
        monkeypatch.setenv("ALPACA_EXPECTED_DEV_ACCOUNT_ID", "SAME-123")
        monkeypatch.setenv("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "SAME-123")

        with pytest.raises(env.EnvError, match="must be different"):
            env.expected_account_id("dev")


class TestParity:
    def _write(self, tmp_path, body):
        p = tmp_path / ".env"
        p.write_text(body)
        return p

    def test_flags_inline_comment(self, tmp_path):
        p = self._write(tmp_path, "ALPACA_ENV=dev   # dev | scored\n")
        ok, problems = env.parity_report(p)
        assert not ok
        assert any("parses differently" in x for x in problems)

    def test_clean_file_passes(self, tmp_path):
        p = self._write(tmp_path, "# a comment\nALPACA_ENV=dev\nALPACA_PAPER_TRADE=true\n")
        ok, problems = env.parity_report(p)
        assert ok, problems

    def test_flags_duplicate_key(self, tmp_path):
        p = self._write(tmp_path, "A=1\nB=2\nA=3\n")
        ok, problems = env.parity_report(p)
        assert not ok
        assert any("defined twice" in x for x in problems)

    def test_secrets_are_redacted_in_problem_text(self, tmp_path):
        p = self._write(tmp_path, "ALPACA_SECRET_KEY=hunter2   # do not leak\n")
        _, problems = env.parity_report(p)
        blob = " ".join(problems)
        assert "hunter2" not in blob
        assert "<redacted>" in blob


class TestPreflight:
    def test_rejects_non_paper(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")
        monkeypatch.setenv("ALPACA_ENV", "dev")
        problems = env.preflight(tmp_path / "missing.env", strict=False)
        assert any("ALPACA_PAPER_TRADE" in p for p in problems)

    def test_rejects_live_base_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
        monkeypatch.setenv("ALPACA_ENV", "dev")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        problems = env.preflight(tmp_path / "missing.env", strict=False)
        assert any("paper endpoint" in p for p in problems)

    def test_strict_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER_TRADE", "nope")
        with pytest.raises(env.EnvError):
            env.preflight(tmp_path / "missing.env", strict=True)

    def test_repo_dotenv_example_is_parseable_by_both(self):
        """The shipped template must never reintroduce the pattern."""
        ok, problems = env.parity_report(".env.example")
        assert ok, problems


# -- .env must come from this repository, never from up the tree ---------------


def _dotenv_calls():
    """Every real load_dotenv() call, via AST so prose about it is not matched."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found = []
    for path in [root / "main.py", *sorted((root / "tools").glob("*.py"))]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "load_dotenv":
                found.append((path.name, node.args))
    return found


def test_no_entry_point_searches_parent_directories_for_a_dotenv():
    """python-dotenv walks parent directories by default. With no .env in the
    repo it silently loaded one from outside the project, so the agent could
    run against whichever account a stray file two levels up named -- while
    looking exactly like it was using project config."""
    bare = [name for name, args in _dotenv_calls() if not args]
    assert not bare, f"these call load_dotenv() with no path, so they search upward: {bare}"


def test_every_dotenv_load_is_anchored_to_this_file_tree():
    """Anchored means derived from __file__, whether directly or through a
    ROOT constant -- not an absolute path or a bare relative name."""
    import ast

    unanchored = []
    for name, args in _dotenv_calls():
        source = ast.dump(args[0])
        if "__file__" not in source and "ROOT" not in source:
            unanchored.append(name)
    assert not unanchored, f"these load a .env not anchored to the repo: {unanchored}"


def test_the_calls_are_actually_found():
    """Guard against the guard: an AST walk that matches nothing would pass
    both tests above while proving nothing."""
    assert len(_dotenv_calls()) >= 4
