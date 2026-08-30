"""The dashboard is the Application URL a judge opens. It must not 500.

Two properties matter more than looks:

  1. It never crashes on a journal in any state -- missing, empty, or with a
     torn tail from a crash. A judge opening the link during a restart must see
     a page, not a stack trace.
  2. It exposes no credentials and offers no write path. It reads one file.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("GLASSBOX_JOURNAL_PATH", str(path))
    import importlib

    import dashboard.app as mod

    importlib.reload(mod)
    return TestClient(mod.app), path


def seed(path, n=3):
    from glassbox.journal import Journal

    j = Journal(path)
    j.append("broker", "STARTUP", {"account_number": "PA-TEST", "equity": "100000", "env": "dev"})
    j.append(
        "risk.kernel",
        "PLAN_REFUSED",
        {
            "reason": "naked short option: maximum loss is unbounded",
            "failed_invariant": "02_bounded_max_loss",
        },
    )
    j.append(
        "thesis.llm",
        "CANDIDATE_SELECTED",
        {"candidate_id": "candidate-spy", "rationale": "Best bounded setup."},
    )
    j.append(
        "thesis.llm",
        "CANDIDATE_ABSTAINED",
        {"reason": "No second setup clears the bar."},
    )
    j.append("risk.kernel", "PLAN_APPROVED", {"symbol": "SPY260904C00783000", "checks_passed": 13})
    for i, e in enumerate([100000, 101000, 99500]):
        j.append("scheduler", "HEARTBEAT", {"equity": e, "i": i})
    return j


ROUTES = [
    "/",
    "/healthz",
    "/api/summary",
    "/api/verify",
    "/api/equity",
    "/api/calendar",
    "/api/journal",
]


class TestResilience:
    def test_every_route_works_with_no_journal_at_all(self, client):
        c, _ = client
        for r in ROUTES:
            assert c.get(r).status_code == 200, r

    def test_every_route_works_with_an_empty_journal(self, client):
        c, path = client
        path.write_text("")
        for r in ROUTES:
            assert c.get(r).status_code == 200, r

    def test_every_route_works_with_a_torn_tail(self, client):
        """A judge refreshing mid-restart must not see a stack trace."""
        c, path = client
        seed(path)
        with path.open("a") as fh:
            fh.write('{"seq": 99, "ts": "2026-09-0')
        for r in ROUTES:
            assert c.get(r).status_code == 200, r

    def test_reports_a_broken_chain_rather_than_hiding_it(self, client):
        c, path = client
        seed(path)
        lines = path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["payload"]["reason"] = "edited"
        lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        v = c.get("/api/verify").json()
        assert v["ok"] is False
        assert c.get("/").status_code == 200


class TestContent:
    def test_summary_counts_refusals(self, client):
        c, path = client
        seed(path)
        s = c.get("/api/summary").json()
        assert s["plans_refused"] == 1
        assert s["plans_approved"] == 1
        assert s["plans_reviewed"] == 2
        assert s["candidate_selections"] == 1
        assert s["candidate_abstentions"] == 1
        assert s["chain_ok"] is True
        assert s["equity"] == 99500.0

    def test_page_describes_the_actual_bounded_options_only_path(self, client):
        c, _ = client
        body = c.get("/").text.lower()

        assert "options-only scored path" in body
        assert "pre-priced" in body
        assert "select one candidate or abstain" in body
        assert "alpaca trading api" in body
        assert "risk kernel" in body
        assert "model writes the thesis" not in body
        assert "what the model proposed" not in body
        assert "mcp" not in body

    def test_page_discloses_unperformed_external_gates(self, client):
        c, _ = client
        body = c.get("/").text.lower()

        assert "dev venue proof pending" in body
        assert "vps soak pending" in body

    def test_equity_series_comes_from_heartbeats(self, client):
        c, path = client
        seed(path)
        pts = c.get("/api/equity").json()
        assert [p["equity"] for p in pts] == [100000.0, 101000.0, 99500.0]

    def test_calendar_marks_payrolls_out_of_window(self, client):
        """The correction that reshaped the strategy, visible to a judge."""
        c, _ = client
        cal = c.get("/api/calendar").json()
        payrolls = [e for e in cal["events"] if "Employment Situation" in e["name"]]
        assert payrolls and payrolls[0]["in_window"] is False
        assert "Thu 03 Sep" in cal["measurement"]

    def test_journal_defaults_to_headline_events(self, client):
        c, path = client
        seed(path)
        headline = c.get("/api/journal").json()
        assert not any(r["event"] == "HEARTBEAT" for r in headline)
        every = c.get("/api/journal?all_events=true").json()
        assert any(r["event"] == "HEARTBEAT" for r in every)


class TestSafety:
    def test_no_write_routes_exist(self, client):
        c, _ = client
        import dashboard.app as mod

        methods = set()
        for route in mod.app.routes:
            methods |= set(getattr(route, "methods", set()) or set())
        assert methods <= {"GET", "HEAD"}, f"dashboard exposes {methods}"

    def test_page_does_not_leak_credentials(self, client, monkeypatch):
        monkeypatch.setenv("ALPACA_SECRET_KEY", "SUPERSECRETVALUE")
        c, path = client
        seed(path)
        for r in ROUTES:
            assert "SUPERSECRETVALUE" not in c.get(r).text
