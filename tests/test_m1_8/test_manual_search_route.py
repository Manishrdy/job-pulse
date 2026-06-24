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
