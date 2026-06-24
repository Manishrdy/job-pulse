"""M3-1 — source filter in list_jobs, sidebar filter, and card badge."""

from __future__ import annotations

import sqlite3

from jobpulse.services import jobs_service


def _seed_pair(conn, seed):
    seed(conn, global_id="gh:jh1", source="jobhive", title="Software Engineer")
    seed(conn, global_id="gh:gs1", source="google_search", title="Software Engineer")


# ── service filter ─────────────────────────────────────────────────────────


def test_list_jobs_filters_by_source(test_db: sqlite3.Connection, seed):
    _seed_pair(test_db, seed)

    google = jobs_service.list_jobs(test_db, source="google_search")
    assert google["total"] == 1
    assert google["jobs"][0]["global_id"] == "gh:gs1"

    jobhive = jobs_service.list_jobs(test_db, source="jobhive")
    assert jobhive["total"] == 1
    assert jobhive["jobs"][0]["global_id"] == "gh:jh1"


def test_no_source_filter_returns_both(test_db: sqlite3.Connection, seed):
    _seed_pair(test_db, seed)
    assert jobs_service.list_jobs(test_db)["total"] == 2


def test_source_combines_with_other_filters(test_db: sqlite3.Connection, seed):
    seed(test_db, global_id="gh:gs1", source="google_search", title="AI Engineer")
    seed(test_db, global_id="gh:gs2", source="google_search", title="Designer")
    res = jobs_service.list_jobs(test_db, source="google_search", role="Engineer")
    assert res["total"] == 1
    assert res["jobs"][0]["global_id"] == "gh:gs1"


# ── feed UI ────────────────────────────────────────────────────────────────


def test_source_filter_dropdown_rendered(client):
    html = client.get("/").text
    assert 'name="source"' in html
    assert 'value="google_search"' in html


def test_google_badge_on_card(client, test_config, seed):
    from jobpulse.database import get_connection

    conn = get_connection(test_config.database.path)
    seed(conn, global_id="gh:gs1", source="google_search", title="AI Engineer")
    seed(conn, global_id="gh:jh1", source="jobhive", title="Backend Engineer")
    conn.close()

    html = client.get("/").text
    assert "badge-google" in html  # the google card carries the badge


def test_source_filter_applies_via_route(client, test_config, seed):
    from jobpulse.database import get_connection

    conn = get_connection(test_config.database.path)
    seed(conn, global_id="gh:gs1", source="google_search", title="AI Engineer", company="GCorp")
    seed(conn, global_id="gh:jh1", source="jobhive", title="Backend Engineer", company="JCorp")
    conn.close()

    html = client.get("/partials/jobs?source=google_search").text
    assert "GCorp" in html
    assert "JCorp" not in html
