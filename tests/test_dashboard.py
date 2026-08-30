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


# -- performance and lineage (evidence UX) ------------------------------------


def _journal_with(tmp_path, monkeypatch, records):
    """Point the dashboard at a journal containing exactly these events."""
    import dashboard.app as app_module
    from glassbox.journal import Journal

    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    for actor, event, payload in records:
        journal.append(actor, event, payload)
    monkeypatch.setattr(app_module, "JOURNAL_PATH", str(path))
    return app_module


def test_performance_endpoint_measures_the_heartbeat_equity_curve(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [
            ("scheduler", "HEARTBEAT", {"equity": "100000"}),
            ("scheduler", "HEARTBEAT", {"equity": "103000"}),
            ("scheduler", "HEARTBEAT", {"equity": "101000"}),
        ],
    )
    body = TestClient(app_module.app).get("/api/performance").json()

    assert body["starting_equity"] == "100000"
    assert body["ending_equity"] == "101000"
    assert body["absolute_pnl"] == "1000"
    # Peak 103000 -> trough 101000 is a real decline the total return hides.
    assert body["max_drawdown_pct"] < 0
    assert body["source"] == "journal heartbeats"


def test_performance_endpoint_marks_a_short_window_indicative(tmp_path, monkeypatch):
    """The panel must never show a confident Sharpe from three heartbeats."""
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [("scheduler", "HEARTBEAT", {"equity": str(100000 + i * 500)}) for i in range(3)],
    )
    body = TestClient(app_module.app).get("/api/performance").json()

    assert body["ratios_are_indicative"] is True
    assert body["notes"], "a ratio was published with no caveat attached"


def test_lineage_shows_the_whole_decision_chain_for_one_plan(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [
            ("scheduler", "CANDIDATE_SET_BUILT", {"plan_id": "gbp-1", "count": 2}),
            ("thesis", "CANDIDATE_SELECTED", {"plan_id": "gbp-1", "candidate_id": "gbp-1"}),
            ("kernel", "PLAN_APPROVED", {"plan_id": "gbp-1", "reason": "all invariants"}),
            ("execute", "ORDER_SUBMIT_INTENT", {"plan_id": "gbp-1", "symbol": "SPY", "qty": "2"}),
            ("broker", "ORDER_ACCEPTED", {"plan_id": "gbp-1", "broker_order_id": "abc"}),
        ],
    )
    rows = TestClient(app_module.app).get("/api/lineage").json()

    assert len(rows) == 1
    events = [step["event"] for step in rows[0]["steps"]]
    assert events == [
        "CANDIDATE_SET_BUILT",
        "CANDIDATE_SELECTED",
        "PLAN_APPROVED",
        "ORDER_SUBMIT_INTENT",
        "ORDER_ACCEPTED",
    ]
    assert all(step["label"] for step in rows[0]["steps"])


def test_lineage_keeps_abstentions_and_refusals_visible(tmp_path, monkeypatch):
    """The steps that prove the AI could not act alone are the ones a judge
    most needs to see, so they must never be filtered out as noise."""
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [
            ("scheduler", "CANDIDATE_SET_BUILT", {"plan_id": "gbp-2", "count": 2}),
            ("thesis", "CANDIDATE_ABSTAINED", {"plan_id": "gbp-2", "reason": "model timeout"}),
        ],
    )
    rows = TestClient(app_module.app).get("/api/lineage").json()

    labels = [s["event"] for s in rows[0]["steps"]]
    assert "CANDIDATE_ABSTAINED" in labels
    assert "model timeout" in rows[0]["steps"][-1]["detail"]


def test_lineage_detail_never_carries_raw_untrusted_text_unbounded(tmp_path, monkeypatch):
    """Model and provider text is untrusted. It is truncated here and escaped
    in the page; an unbounded passthrough would be a stored-XSS surface."""
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [
            (
                "thesis",
                "CANDIDATE_ABSTAINED",
                {"plan_id": "gbp-3", "reason": "<script>alert(1)</script>" + "A" * 500},
            )
        ],
    )
    rows = TestClient(app_module.app).get("/api/lineage").json()
    detail = rows[0]["steps"][0]["detail"]

    assert len(detail) <= 120
    page = TestClient(app_module.app).get("/").text
    assert "esc(st.detail)" in page, "lineage detail is rendered without escaping"


def test_lineage_limit_is_bounded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    app_module = _journal_with(
        tmp_path,
        monkeypatch,
        [("scheduler", "CANDIDATE_SET_BUILT", {"plan_id": f"gbp-{i}"}) for i in range(80)],
    )
    client = TestClient(app_module.app)
    assert len(client.get("/api/lineage?limit=5").json()) == 5
    assert len(client.get("/api/lineage?limit=9999").json()) <= 50
