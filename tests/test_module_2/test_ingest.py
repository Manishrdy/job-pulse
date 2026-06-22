from __future__ import annotations

import sqlite3

from jobpulse.ingest import ingest_jobs, load_blocklist, record_scrape_run
from tests.conftest import make_record

ROLES = ["Software Engineer", "Backend Engineer"]


def test_new_job_inserts(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:1", title="Software Engineer")
    stats = ingest_jobs(test_db, [rec], target_roles=ROLES)

    assert stats.inserted == 1
    assert stats.updated == 0
    assert stats.total == 1

    row = test_db.execute("SELECT * FROM jobs WHERE global_id = 'greenhouse:1'").fetchone()
    assert row is not None
    assert row["title"] == "Software Engineer"
    assert row["first_seen"] is not None
    assert row["last_seen"] is not None


def test_duplicate_global_id_updates_last_seen_no_new_row(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:dup", title="Software Engineer")

    ingest_jobs(test_db, [rec], target_roles=ROLES)
    stats2 = ingest_jobs(test_db, [rec], target_roles=ROLES)

    assert stats2.inserted == 0
    assert stats2.updated == 1

    count = test_db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE global_id = 'greenhouse:dup'"
    ).fetchone()["c"]
    assert count == 1


def test_blocked_company_inserted_with_flag(test_db: sqlite3.Connection):
    test_db.execute("INSERT INTO company_blocklist (company) VALUES ('Bad Corp')")
    test_db.commit()

    rec = make_record(global_id="greenhouse:b1", company="Bad Corp")
    stats = ingest_jobs(test_db, [rec], target_roles=ROLES)

    assert stats.blocked == 1
    row = test_db.execute("SELECT is_blocked FROM jobs WHERE global_id = 'greenhouse:b1'").fetchone()
    assert row["is_blocked"] == 1


def test_non_blocked_company_flag_zero(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:ok", company="Good Corp")
    ingest_jobs(test_db, [rec], target_roles=ROLES)
    row = test_db.execute("SELECT is_blocked FROM jobs WHERE global_id = 'greenhouse:ok'").fetchone()
    assert row["is_blocked"] == 0


def test_relevance_stored_for_matching(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:rel", title="Software Engineer")
    ingest_jobs(test_db, [rec], target_roles=ROLES)
    row = test_db.execute(
        "SELECT relevance_score FROM jobs WHERE global_id = 'greenhouse:rel'"
    ).fetchone()
    assert row["relevance_score"] > 0


def test_relevance_zero_for_non_matching(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:norel", title="Marketing Manager")
    ingest_jobs(test_db, [rec], target_roles=ROLES)
    row = test_db.execute(
        "SELECT relevance_score FROM jobs WHERE global_id = 'greenhouse:norel'"
    ).fetchone()
    assert row["relevance_score"] == 0.0


def test_blocklist_refreshed_on_update(test_db: sqlite3.Connection):
    rec = make_record(global_id="greenhouse:upd", company="Later Bad")
    ingest_jobs(test_db, [rec], target_roles=ROLES)

    # Now block the company and re-ingest the same job.
    test_db.execute("INSERT INTO company_blocklist (company) VALUES ('Later Bad')")
    test_db.commit()
    ingest_jobs(test_db, [rec], target_roles=ROLES)

    row = test_db.execute("SELECT is_blocked FROM jobs WHERE global_id = 'greenhouse:upd'").fetchone()
    assert row["is_blocked"] == 1


def test_load_blocklist(test_db: sqlite3.Connection):
    test_db.execute("INSERT INTO company_blocklist (company) VALUES ('A'), ('B')")
    test_db.commit()
    assert load_blocklist(test_db) == {"A", "B"}


def test_null_fields_ingest_gracefully(test_db: sqlite3.Connection):
    rec = make_record(
        global_id="greenhouse:nulls",
        description=None,
        posted_at=None,
        salary_min=None,
        salary_max=None,
        location=None,
    )
    stats = ingest_jobs(test_db, [rec], target_roles=ROLES)
    assert stats.inserted == 1


def test_record_scrape_run_counts(test_db: sqlite3.Connection):
    run_id = record_scrape_run(
        test_db,
        schedule_slot="morning",
        ats_types_scraped=["greenhouse", "lever"],
        jobs_fetched=100,
        jobs_inserted=40,
        jobs_updated=25,
        jobs_deleted=5,
        duration_seconds=12.5,
        status="success",
    )
    row = test_db.execute("SELECT * FROM scrape_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["schedule_slot"] == "morning"
    assert row["ats_types_scraped"] == "greenhouse,lever"
    assert row["jobs_fetched"] == 100
    assert row["jobs_inserted"] == 40
    assert row["jobs_updated"] == 25
    assert row["jobs_deleted"] == 5
    assert row["duration_seconds"] == 12.5
    assert row["status"] == "success"
    assert row["error_msg"] is None


def test_record_scrape_run_accepts_string_ats(test_db: sqlite3.Connection):
    run_id = record_scrape_run(
        test_db,
        schedule_slot="manual",
        ats_types_scraped="greenhouse",
        jobs_fetched=0,
        jobs_inserted=0,
        jobs_updated=0,
        status="failure",
        error_msg="boom",
    )
    row = test_db.execute("SELECT * FROM scrape_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["ats_types_scraped"] == "greenhouse"
    assert row["status"] == "failure"
    assert row["error_msg"] == "boom"
