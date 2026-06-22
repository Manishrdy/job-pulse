from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from jobpulse.cleanup import cleanup_old_jobs, expire_job, run_cleanup

# A pinned reference "now" so day-boundary math is deterministic. Both the
# inserted first_seen values and cleanup's cutoff derive from this instant.
REF = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def ts(**delta) -> str:
    """Format REF +/- a timedelta in the stored Z timestamp format."""
    return (REF + timedelta(**delta)).strftime(TS_FMT)


def insert_job(conn: sqlite3.Connection, global_id: str, first_seen: str, **extra) -> int:
    cols = {
        "global_id": global_id,
        "url": "https://example.com",
        "title": "Software Engineer",
        "company": "Acme",
        "ats_type": "greenhouse",
        "first_seen": first_seen,
        "last_seen": first_seen,
        "status": extra.get("status", "active"),
    }
    cur = conn.execute(
        """INSERT INTO jobs (global_id, url, title, company, ats_type, first_seen, last_seen, status)
           VALUES (:global_id, :url, :title, :company, :ats_type, :first_seen, :last_seen, :status)""",
        cols,
    )
    conn.commit()
    return cur.lastrowid


def test_jobs_older_than_ttl_deleted(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:old", ts(days=-4))
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 1
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0


def test_jobs_exactly_ttl_old_not_deleted(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:exact", ts(days=-3))
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 0
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_jobs_ttl_plus_one_second_deleted(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:edge", ts(days=-3, seconds=-1))
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 1


def test_recent_jobs_kept(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:fresh", ts(days=-1))
    insert_job(test_db, "gh:today", ts(hours=-2))
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 0
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 2


def test_mixed_ages(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:a", ts(days=-5))   # delete
    insert_job(test_db, "gh:b", ts(days=-3))   # keep (boundary)
    insert_job(test_db, "gh:c", ts(days=-1))   # keep
    insert_job(test_db, "gh:d", ts(days=-10))  # delete
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 2
    remaining = {r["global_id"] for r in test_db.execute("SELECT global_id FROM jobs")}
    assert remaining == {"gh:b", "gh:c"}


def test_applied_jobs_never_deleted(test_db: sqlite3.Connection):
    test_db.execute(
        """INSERT INTO applied_jobs (global_id, url, title, company, ats_type, first_seen)
           VALUES ('gh:applied', 'https://example.com', 'SWE', 'Acme', 'greenhouse', ?)""",
        (ts(days=-365),),
    )
    test_db.commit()
    cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    count = test_db.execute("SELECT COUNT(*) c FROM applied_jobs").fetchone()["c"]
    assert count == 1


def test_expired_jobs_still_subject_to_ttl(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:exp_old", ts(days=-4), status="expired")
    insert_job(test_db, "gh:exp_new", ts(days=-1), status="expired")
    deleted = cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    assert deleted == 1
    remaining = {r["global_id"] for r in test_db.execute("SELECT global_id FROM jobs")}
    assert remaining == {"gh:exp_new"}


def test_cleanup_removes_from_fts(test_db: sqlite3.Connection):
    insert_job(test_db, "gh:fts", ts(days=-5))
    cleanup_old_jobs(test_db, ttl_days=3, now=REF)
    hits = test_db.execute("SELECT * FROM jobs_fts WHERE jobs_fts MATCH 'Software'").fetchall()
    assert len(hits) == 0


def test_run_cleanup_records_scrape_run(test_db: sqlite3.Connection, test_config):
    insert_job(test_db, "gh:1", ts(days=-4))
    insert_job(test_db, "gh:2", ts(days=-9))
    insert_job(test_db, "gh:3", ts(days=-1))

    deleted = run_cleanup(test_db, test_config, now=REF)
    assert deleted == 2

    row = test_db.execute(
        "SELECT * FROM scrape_runs WHERE schedule_slot = 'cleanup'"
    ).fetchone()
    assert row is not None
    assert row["jobs_deleted"] == 2
    assert row["status"] == "success"


def test_cleanup_empty_table(test_db: sqlite3.Connection):
    assert cleanup_old_jobs(test_db, ttl_days=3, now=REF) == 0


def test_cleanup_uses_config_ttl(test_db: sqlite3.Connection, test_config):
    # test_config has ttl_days=3; a 2-day-old job survives.
    insert_job(test_db, "gh:cfg", ts(days=-2))
    deleted = run_cleanup(test_db, test_config, now=REF)
    assert deleted == 0


# --- expire_job ------------------------------------------------------------


def test_expire_job_sets_status_and_timestamp(test_db: sqlite3.Connection):
    job_id = insert_job(test_db, "gh:tobeexpired", ts(hours=-1))
    assert expire_job(test_db, job_id) is True

    row = test_db.execute("SELECT status, expired_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "expired"
    assert row["expired_at"] is not None


def test_expire_nonexistent_job_returns_false(test_db: sqlite3.Connection):
    assert expire_job(test_db, 99999) is False


def test_expire_already_expired_returns_false(test_db: sqlite3.Connection):
    job_id = insert_job(test_db, "gh:already", ts(hours=-1), status="expired")
    assert expire_job(test_db, job_id) is False
