"""M1-1 — Phase 2 schema & model foundation.

Verifies the additive schema changes land on both fresh and legacy
(Phase 1) databases without disturbing existing data, and that the
``source`` column round-trips through :class:`JobRecord` / ``ingest_jobs``.
"""

from __future__ import annotations

import sqlite3

import pytest

from jobpulse.database import get_connection, init_db
from jobpulse.ingest import ingest_jobs
from jobpulse.models import INSERT_COLUMNS, JobRecord


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


# ── Fresh DB ────────────────────────────────────────────────────────────


def test_jobs_has_source_column(test_db: sqlite3.Connection):
    assert "source" in _columns(test_db, "jobs")


def test_source_defaults_to_jobhive(test_db: sqlite3.Connection, seed):
    job_id = seed(test_db)
    row = test_db.execute("SELECT source FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["source"] == "jobhive"


def test_idx_jobs_url_exists(test_db: sqlite3.Connection):
    assert "idx_jobs_url" in _indexes(test_db, "jobs")


def test_search_runs_table(test_db: sqlite3.Connection):
    cols = _columns(test_db, "search_runs")
    assert {
        "id", "run_at", "schedule_slot", "queries_executed", "urls_found",
        "urls_new", "jobs_inserted", "jobs_skipped_dedup",
        "jobs_skipped_blocked", "duration_seconds", "status", "error_msg",
    } <= cols


def test_search_results_cache_table(test_db: sqlite3.Connection):
    assert {"id", "query_hash", "url", "discovered_at"} <= _columns(
        test_db, "search_results_cache"
    )


def test_search_results_cache_unique_constraint(test_db: sqlite3.Connection):
    test_db.execute(
        "INSERT INTO search_results_cache (query_hash, url) VALUES (?, ?)",
        ("h1", "https://x/jobs/1"),
    )
    test_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        test_db.execute(
            "INSERT INTO search_results_cache (query_hash, url) VALUES (?, ?)",
            ("h1", "https://x/jobs/1"),
        )


# ── Model ───────────────────────────────────────────────────────────────


def test_jobrecord_source_default():
    rec = JobRecord(
        global_id="greenhouse:1", url="https://x/jobs/1", title="SWE",
        company="Acme", ats_type="greenhouse",
    )
    assert rec.source == "jobhive"


def test_source_in_insert_columns():
    assert "source" in INSERT_COLUMNS
    # insert_values stays aligned with INSERT_COLUMNS order.
    rec = JobRecord(
        global_id="greenhouse:1", url="https://x/jobs/1", title="SWE",
        company="Acme", ats_type="greenhouse", source="google_search",
    )
    values = rec.insert_values()
    assert values[INSERT_COLUMNS.index("source")] == "google_search"


def test_from_jobhive_keeps_jobhive_source(jobhive_job_factory):
    rec = JobRecord.from_jobhive(jobhive_job_factory())
    assert rec.source == "jobhive"


def test_google_source_roundtrips_through_ingest(test_db: sqlite3.Connection):
    rec = JobRecord(
        global_id="greenhouse:999", url="https://boards.greenhouse.io/acme/jobs/999",
        title="Backend Engineer", company="Acme", ats_type="greenhouse",
        ats_id="999", source="google_search",
    )
    stats = ingest_jobs(test_db, [rec], target_roles=["Backend Engineer"])
    assert stats.inserted == 1
    row = test_db.execute(
        "SELECT source FROM jobs WHERE global_id = ?", ("greenhouse:999",)
    ).fetchone()
    assert row["source"] == "google_search"


# ── Legacy (Phase 1) DB migration ───────────────────────────────────────


def test_migration_adds_source_and_backfills(test_config):
    """A pre-Phase-2 jobs table (no source column) gains it, and existing
    rows backfill to 'jobhive'."""
    path = test_config.database.path
    # Stand up a legacy jobs table without the source column, with one row.
    conn = get_connection(path)
    # Phase 1 jobs shape, minus the new `source` column (and minus the
    # columns the migration list already covers). Includes the columns the
    # SCHEMA_SQL indexes / FTS triggers reference so init_db can run.
    conn.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_id TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            ats_type TEXT NOT NULL,
            location TEXT,
            description TEXT,
            posted_at TIMESTAMP,
            first_seen TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs (global_id, url, title, company, ats_type) "
        "VALUES ('gh:legacy', 'https://x/jobs/legacy', 'SWE', 'Acme', 'greenhouse')"
    )
    conn.commit()
    conn.close()

    # init_db should migrate in place (CREATE TABLE IF NOT EXISTS is a no-op
    # on the existing table; _apply_migrations adds the column).
    migrated = init_db(test_config)
    try:
        assert "source" in _columns(migrated, "jobs")
        row = migrated.execute(
            "SELECT source FROM jobs WHERE global_id = 'gh:legacy'"
        ).fetchone()
        assert row["source"] == "jobhive"
        assert {"search_runs", "search_results_cache"} <= _tables(migrated)
    finally:
        migrated.close()
