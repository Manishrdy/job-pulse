from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from jobpulse.services import analytics_service, applied_service
from tests.conftest import seed_job

REF = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
ROLES = ["Software Engineer", "Backend Engineer"]


# --- Page ------------------------------------------------------------------


def test_analytics_page_returns_200(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    resp = client.get("/analytics")
    assert resp.status_code == 200
    assert "Analytics" in resp.text


def test_analytics_page_has_chart_canvases(client: TestClient):
    html = client.get("/analytics").text
    for cid in ("chart-apps-day", "chart-funnel", "chart-ats", "chart-scrape"):
        assert f'id="{cid}"' in html
    assert "JOBPULSE_ANALYTICS" in html  # embedded data
    assert "chart.js" in html.lower() or "chart.umd" in html.lower()


def test_analytics_page_summary_cards(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    seed_job(test_db, title="Backend Engineer")
    html = client.get("/analytics").text
    assert "Active jobs" in html
    assert "Total applied" in html
    assert "Response rate" in html


def test_analytics_role_table(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Backend Engineer")
    html = client.get("/analytics").text
    assert "Role-wise breakdown" in html
    assert "Backend Engineer" in html


def test_analytics_empty_state(client: TestClient):
    html = client.get("/analytics").text
    assert "No data yet" in html


def test_analytics_range_pills(client: TestClient):
    html = client.get("/analytics").text
    assert "Last 7 days" in html
    assert "/analytics?days=7" in html
    # selected pill marked active
    assert 'class="range-pill active"' in client.get("/analytics", params={"days": 7}).text


# --- API JSON structure ----------------------------------------------------


def test_api_summary_structure(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    body = client.get("/api/analytics/summary").json()
    assert set(body["cards"]) == {
        "total_active_jobs", "total_applied", "applications_this_week", "response_rate",
        "google_only_finds",
    }
    for key in ("role_breakdown", "applications_per_day", "ats_breakdown", "status_funnel",
                "scrape_trends", "source_breakdown"):
        assert key in body
    assert "jobs" in body["ats_breakdown"] and "applied" in body["ats_breakdown"]


def test_api_summary_empty(client: TestClient):
    body = client.get("/api/analytics/summary").json()
    assert body["cards"]["total_applied"] == 0
    assert body["cards"]["response_rate"] == 0.0
    assert body["applications_per_day"] == []


# --- Date-range filtering --------------------------------------------------


def _apply_on(test_db: sqlite3.Connection, applied_at: str, gid: str) -> None:
    jid = seed_job(test_db, global_id=gid)
    aid = applied_service.mark_applied(test_db, jid)
    test_db.execute("UPDATE applied_jobs SET applied_at = ? WHERE id = ?", (applied_at, aid))
    test_db.commit()


def test_applications_per_day_date_filter(test_db: sqlite3.Connection):
    _apply_on(test_db, "2026-06-21T12:00:00Z", "gh:recent")   # within 7d of REF
    _apply_on(test_db, "2026-06-01T12:00:00Z", "gh:old")      # outside 7d

    all_rows = analytics_service.applications_per_day(test_db)
    assert sum(r["count"] for r in all_rows) == 2

    last7 = analytics_service.applications_per_day(test_db, days=7, now=REF)
    assert sum(r["count"] for r in last7) == 1


def test_scrape_trends_date_filter(test_db: sqlite3.Connection):
    test_db.execute(
        "INSERT INTO scrape_runs (run_at, ats_types_scraped, jobs_inserted, status) "
        "VALUES ('2026-06-20T08:00:00Z', 'greenhouse', 10, 'success')"
    )
    test_db.execute(
        "INSERT INTO scrape_runs (run_at, ats_types_scraped, jobs_inserted, status) "
        "VALUES ('2026-05-01T08:00:00Z', 'greenhouse', 99, 'success')"
    )
    test_db.commit()

    all_trend = analytics_service.scrape_trends(test_db)
    assert sum(t["inserted"] for t in all_trend) == 109

    last7 = analytics_service.scrape_trends(test_db, days=7, now=REF)
    assert sum(t["inserted"] for t in last7) == 10


def test_summary_threads_days(test_db: sqlite3.Connection):
    _apply_on(test_db, "2026-06-21T12:00:00Z", "gh:r1")
    _apply_on(test_db, "2026-06-01T12:00:00Z", "gh:r2")
    scoped = analytics_service.summary(test_db, ROLES, days=7, now=REF)
    assert scoped["days"] == 7
    assert sum(r["count"] for r in scoped["applications_per_day"]) == 1


def test_api_summary_days_param(client: TestClient, test_db: sqlite3.Connection):
    _apply_on(test_db, "2026-06-21T12:00:00Z", "gh:api1")
    body = client.get("/api/analytics/summary", params={"days": 7}).json()
    assert body["days"] == 7
