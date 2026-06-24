"""M1-8 (reworked) — the no-input "Search Internet" button + route."""

from __future__ import annotations

import pytest

from jobpulse.routes import pages


@pytest.fixture
def captured_runs(monkeypatch):
    """Capture background-search invocations instead of running them."""
    calls: list[dict] = []

    def fake_run(config, *, queries, schedule_slot="manual"):
        calls.append({"queries": list(queries), "slot": schedule_slot})
        return True

    monkeypatch.setattr(
        pages.google_pipeline, "run_google_search_in_background", fake_run
    )
    return calls


def test_scrape_logs_page_shows_search_button(client):
    html = client.get("/scrape-logs").text
    # Button, not a text box.
    assert 'hx-post="/internet-search/run"' in html
    assert "Search Internet" in html
    assert 'name="query"' not in html  # the typed box is gone


def test_button_triggers_full_matrix_search(client, captured_runs):
    resp = client.post("/internet-search/run")
    assert resp.status_code == 200
    assert len(captured_runs) == 1
    run = captured_runs[0]
    assert run["slot"] == "manual"
    # Queries are auto-generated from config — non-empty, and shaped like
    # `site:{domain} "{role}" "{location}"` using a config role + mapped domain.
    assert run["queries"], "expected auto-generated queries"
    joined = "\n".join(run["queries"])
    assert "site:" in joined
    assert '"Software Engineer"' in joined or '"Backend Engineer"' in joined  # config roles
    assert "boards.greenhouse.io" in joined or "jobs.lever.co" in joined  # config ATS domains


def test_no_query_input_accepted(client, captured_runs):
    # Route takes no form fields; posting one is simply ignored (still runs).
    resp = client.post("/internet-search/run", data={"query": "ignored"})
    assert resp.status_code == 200
    assert len(captured_runs) == 1


def test_feed_polls_during_google_search(client, monkeypatch):
    # While a Google search runs, the feed auto-refreshes (like Phase 1 scrape).
    monkeypatch.setattr(pages.google_pipeline, "is_running", lambda: True)
    html = client.get("/").text
    assert 'hx-trigger="every 4s"' in html
    assert "Finding jobs" in html


def test_scrape_logs_shows_live_search_progress(client, monkeypatch):
    monkeypatch.setattr(
        pages.google_pipeline,
        "get_status",
        lambda: {
            "running": True,
            "progress": {
                "queries_done": 3,
                "queries_total": 40,
                "urls_found": 12,
                "urls_new": 7,
                "inserted": 5,
                "current_query": 'site:jobs.lever.co "AI Engineer" "Austin"',
            },
        },
    )
    html = client.get("/scrape-logs").text
    assert "running: internet search" in html
    assert "URLs found" in html and "Inserted" in html
    assert "3/40" in html  # queries_done/queries_total
    assert "Now searching" in html


def test_search_runs_appear_in_log(client, test_config, captured_runs):
    from jobpulse.database import get_connection

    conn = get_connection(test_config.database.path)
    conn.execute(
        "INSERT INTO search_runs (schedule_slot, queries_executed, urls_found, "
        "urls_new, jobs_inserted, jobs_skipped_dedup, jobs_skipped_blocked, "
        "duration_seconds, status) VALUES ('manual', 1, 5, 3, 2, 1, 0, 4.2, 'success')"
    )
    conn.commit()
    conn.close()

    html = client.get("/scrape-logs").text
    assert "Google search runs" in html
    assert "success" in html
