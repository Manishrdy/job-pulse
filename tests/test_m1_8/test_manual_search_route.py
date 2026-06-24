"""M1-8 — manual Google search UI route on /scrape-logs."""

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


def test_scrape_logs_page_shows_search_form(client):
    resp = client.get("/scrape-logs")
    assert resp.status_code == 200
    assert 'hx-post="/google-search/run"' in resp.text
    assert 'name="query"' in resp.text


def test_post_query_triggers_background_search(client, captured_runs):
    resp = client.post("/google-search/run", data={"query": 'site:jobs.lever.co "AI Engineer"'})
    assert resp.status_code == 200
    assert captured_runs == [
        {"queries": ['site:jobs.lever.co "AI Engineer"'], "slot": "manual"}
    ]


def test_blank_query_does_not_trigger(client, captured_runs):
    resp = client.post("/google-search/run", data={"query": "   "})
    assert resp.status_code == 200
    assert captured_runs == []


def test_search_runs_appear_in_log(client, test_config, captured_runs):
    # Insert a search_runs row directly, then confirm the page renders it.
    from jobpulse.database import get_connection

    conn = get_connection(test_config.database.path)
    conn.execute(
        "INSERT INTO search_runs (schedule_slot, queries_executed, urls_found, "
        "urls_new, jobs_inserted, jobs_skipped_dedup, jobs_skipped_blocked, "
        "duration_seconds, status) VALUES ('manual', 1, 5, 3, 2, 1, 0, 4.2, 'success')"
    )
    conn.commit()
    conn.close()

    resp = client.get("/scrape-logs")
    assert "Google search runs" in resp.text
    assert "success" in resp.text
