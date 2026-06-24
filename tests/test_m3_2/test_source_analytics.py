"""M3-2 — source breakdown + 'Google finds' analytics metric."""

from __future__ import annotations

import sqlite3

from jobpulse.services import analytics_service


def _seed_mix(conn, seed):
    seed(conn, global_id="gh:jh1", source="jobhive")
    seed(conn, global_id="gh:jh2", source="jobhive")
    seed(conn, global_id="gh:gs1", source="google_search")


# ── source_breakdown ───────────────────────────────────────────────────────


def test_source_breakdown_counts(test_db: sqlite3.Connection, seed):
    _seed_mix(test_db, seed)
    bd = {r["source"]: r["count"] for r in analytics_service.source_breakdown(test_db)}
    assert bd == {"jobhive": 2, "google_search": 1}


def test_source_breakdown_excludes_blocked_and_expired(test_db: sqlite3.Connection, seed):
    seed(test_db, global_id="gh:gs1", source="google_search")
    seed(test_db, global_id="gh:gs2", source="google_search", is_blocked=1)
    seed(test_db, global_id="gh:gs3", source="google_search", status="expired")
    bd = {r["source"]: r["count"] for r in analytics_service.source_breakdown(test_db)}
    assert bd == {"google_search": 1}


# ── summary card ───────────────────────────────────────────────────────────


def test_summary_includes_google_only_finds(test_db: sqlite3.Connection, seed):
    _seed_mix(test_db, seed)
    data = analytics_service.summary(test_db, ["Software Engineer"])
    assert data["cards"]["google_only_finds"] == 1
    bd = {r["source"]: r["count"] for r in data["source_breakdown"]}
    assert bd == {"jobhive": 2, "google_search": 1}


def test_google_only_finds_zero_when_no_google(test_db: sqlite3.Connection, seed):
    seed(test_db, global_id="gh:jh1", source="jobhive")
    data = analytics_service.summary(test_db, ["Software Engineer"])
    assert data["cards"]["google_only_finds"] == 0


# ── analytics page render ──────────────────────────────────────────────────


def test_analytics_page_renders_source_section(client, test_config, seed):
    from jobpulse.database import get_connection

    conn = get_connection(test_config.database.path)
    seed(conn, global_id="gh:gs1", source="google_search")
    conn.close()

    html = client.get("/analytics").text
    assert "Google finds" in html
    assert "Jobs by source" in html
    assert "Google Search" in html
