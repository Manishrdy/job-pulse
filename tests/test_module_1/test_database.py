from __future__ import annotations

import sqlite3

import pytest

from jobpulse.config import AppConfig
from jobpulse.database import get_connection, init_db


def test_wal_mode_enabled(test_db: sqlite3.Connection):
    result = test_db.execute("PRAGMA journal_mode").fetchone()
    assert result[0] == "wal"


def test_all_tables_created(test_db: sqlite3.Connection):
    tables = {
        row[0]
        for row in test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "jobs" in tables
    assert "applied_jobs" in tables
    assert "company_blocklist" in tables
    assert "scrape_runs" in tables
    assert "jobs_fts" in tables


def test_indexes_created(test_db: sqlite3.Connection):
    indexes = {
        row[0]
        for row in test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
    }
    expected = {
        "idx_jobs_global_id",
        "idx_jobs_company",
        "idx_jobs_posted_at",
        "idx_jobs_first_seen",
        "idx_jobs_status",
        "idx_jobs_ats_type",
        "idx_applied_global_id",
        "idx_applied_status",
        "idx_applied_applied_at",
        "idx_applied_company",
    }
    assert expected.issubset(indexes)


def test_fts5_table_exists(test_db: sqlite3.Connection):
    result = test_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs_fts'"
    ).fetchone()
    assert result is not None


def test_fts_trigger_on_insert(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, description, location)
           VALUES ('gh:123', 'https://example.com', 'Software Engineer', 'Acme Corp',
                   'greenhouse', 'Build great software', 'San Francisco')"""
    )
    test_db.commit()
    results = test_db.execute(
        "SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'software'"
    ).fetchall()
    assert len(results) == 1
    assert results[0]["title"] == "Software Engineer"


def test_fts_trigger_on_update(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, description)
           VALUES ('gh:456', 'https://example.com', 'Old Title', 'Acme', 'greenhouse', 'desc')"""
    )
    test_db.commit()

    test_db.execute("UPDATE jobs SET title = 'Backend Engineer' WHERE global_id = 'gh:456'")
    test_db.commit()

    old_results = test_db.execute(
        "SELECT * FROM jobs_fts WHERE jobs_fts MATCH '\"Old Title\"'"
    ).fetchall()
    assert len(old_results) == 0

    new_results = test_db.execute(
        "SELECT * FROM jobs_fts WHERE jobs_fts MATCH '\"Backend Engineer\"'"
    ).fetchall()
    assert len(new_results) == 1


def test_fts_trigger_on_delete(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, description)
           VALUES ('gh:789', 'https://example.com', 'Deleted Job', 'Acme', 'greenhouse', 'desc')"""
    )
    test_db.commit()

    test_db.execute("DELETE FROM jobs WHERE global_id = 'gh:789'")
    test_db.commit()

    results = test_db.execute(
        "SELECT * FROM jobs_fts WHERE jobs_fts MATCH '\"Deleted Job\"'"
    ).fetchall()
    assert len(results) == 0


def test_fts_search_across_fields(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, description, location)
           VALUES ('gh:a', 'https://example.com', 'AI Engineer', 'DeepMind',
                   'greenhouse', 'Work on LLMs', 'London')"""
    )
    test_db.commit()

    assert len(test_db.execute("SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'DeepMind'").fetchall()) == 1
    assert len(test_db.execute("SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'LLMs'").fetchall()) == 1
    assert len(test_db.execute("SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'London'").fetchall()) == 1
    assert len(test_db.execute("SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'nonexistent'").fetchall()) == 0


def test_jobs_global_id_unique(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type)
           VALUES ('gh:dup', 'https://example.com', 'Job 1', 'Acme', 'greenhouse')"""
    )
    test_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        test_db.execute(
            """INSERT INTO jobs (global_id, url, title, company, ats_type)
               VALUES ('gh:dup', 'https://other.com', 'Job 2', 'Other', 'greenhouse')"""
        )


def test_jobs_default_values(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type)
           VALUES ('gh:def', 'https://example.com', 'Test Job', 'Acme', 'greenhouse')"""
    )
    test_db.commit()
    row = test_db.execute("SELECT * FROM jobs WHERE global_id = 'gh:def'").fetchone()
    assert row["status"] == "active"
    assert row["is_blocked"] == 0
    assert row["relevance_score"] == 0.0
    assert row["first_seen"] is not None
    assert row["last_seen"] is not None


def test_applied_jobs_global_id_unique(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO applied_jobs (global_id, url, title, company, ats_type, first_seen)
           VALUES ('gh:app1', 'https://example.com', 'Job', 'Acme', 'greenhouse',
                   '2026-01-01T00:00:00Z')"""
    )
    test_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        test_db.execute(
            """INSERT INTO applied_jobs (global_id, url, title, company, ats_type, first_seen)
               VALUES ('gh:app1', 'https://other.com', 'Job 2', 'Other', 'greenhouse',
                       '2026-01-01T00:00:00Z')"""
        )


def test_company_blocklist_unique(test_db: sqlite3.Connection):
    test_db.execute("INSERT INTO company_blocklist (company) VALUES ('Bad Corp')")
    test_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        test_db.execute("INSERT INTO company_blocklist (company) VALUES ('Bad Corp')")


def test_scrape_runs_insert(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO scrape_runs (ats_types_scraped, jobs_fetched, jobs_inserted,
                                    jobs_updated, jobs_deleted, status)
           VALUES ('greenhouse,lever', 100, 50, 30, 5, 'success')"""
    )
    test_db.commit()
    row = test_db.execute("SELECT * FROM scrape_runs").fetchone()
    assert row["jobs_fetched"] == 100
    assert row["jobs_inserted"] == 50
    assert row["status"] == "success"
    assert row["run_at"] is not None


def test_init_db_idempotent(test_config: AppConfig):
    conn1 = init_db(test_config)
    conn1.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type)
           VALUES ('gh:idem', 'https://example.com', 'Job', 'Acme', 'greenhouse')"""
    )
    conn1.commit()
    conn1.close()

    conn2 = init_db(test_config)
    row = conn2.execute("SELECT * FROM jobs WHERE global_id = 'gh:idem'").fetchone()
    assert row is not None
    conn2.close()
