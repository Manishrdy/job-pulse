from __future__ import annotations

import sqlite3

import pytest

from jobpulse.services import applied_service, jobs_service
from tests.conftest import seed_job


def test_mark_applied_moves_job(test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Backend Engineer", company="Acme", global_id="gh:applyme")
    applied_id = applied_service.mark_applied(test_db, jid)

    assert applied_id is not None
    # removed from jobs
    assert jobs_service.get_job(test_db, jid) is None
    # present in applied_jobs with carried fields
    row = test_db.execute("SELECT * FROM applied_jobs WHERE id = ?", (applied_id,)).fetchone()
    assert row["title"] == "Backend Engineer"
    assert row["company"] == "Acme"
    assert row["global_id"] == "gh:applyme"
    assert row["status"] == "applied"
    assert row["applied_at"] is not None


def test_mark_applied_nonexistent(test_db: sqlite3.Connection):
    assert applied_service.mark_applied(test_db, 99999) is None


def test_mark_applied_idempotent(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:dupapply")
    first = applied_service.mark_applied(test_db, jid)

    # Re-seed the same global_id into jobs and apply again.
    jid2 = seed_job(test_db, global_id="gh:dupapply")
    second = applied_service.mark_applied(test_db, jid2)

    assert second == first  # returns existing applied id
    assert jobs_service.get_job(test_db, jid2) is None
    count = test_db.execute(
        "SELECT COUNT(*) c FROM applied_jobs WHERE global_id = 'gh:dupapply'"
    ).fetchone()["c"]
    assert count == 1


def test_list_applied_basic(test_db: sqlite3.Connection):
    for i in range(3):
        jid = seed_job(test_db, global_id=f"gh:a{i}")
        applied_service.mark_applied(test_db, jid)
    result = applied_service.list_applied(test_db)
    assert result["total"] == 3
    assert len(result["jobs"]) == 3


def test_list_applied_filter_status(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:s1")
    aid = applied_service.mark_applied(test_db, jid)
    applied_service.update_applied(test_db, aid, status="interview")

    jid2 = seed_job(test_db, global_id="gh:s2")
    applied_service.mark_applied(test_db, jid2)

    result = applied_service.list_applied(test_db, status="interview")
    assert result["total"] == 1
    assert result["jobs"][0]["id"] == aid


def test_list_applied_search(test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Backend Engineer", global_id="gh:srch1")
    applied_service.mark_applied(test_db, jid)
    jid2 = seed_job(test_db, title="Frontend Developer", global_id="gh:srch2")
    applied_service.mark_applied(test_db, jid2)

    result = applied_service.list_applied(test_db, search="Backend")
    assert result["total"] == 1
    assert result["jobs"][0]["title"] == "Backend Engineer"


def test_update_applied_status(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:u1")
    aid = applied_service.mark_applied(test_db, jid)
    assert applied_service.update_applied(test_db, aid, status="offer") is True
    row = applied_service.get_applied(test_db, aid)
    assert row["status"] == "offer"


def test_update_applied_notes_and_followup(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:u2")
    aid = applied_service.mark_applied(test_db, jid)
    applied_service.update_applied(
        test_db, aid, notes="Called recruiter", follow_up_date="2026-07-01"
    )
    row = applied_service.get_applied(test_db, aid)
    assert row["notes"] == "Called recruiter"
    assert row["follow_up_date"] == "2026-07-01"


def test_update_applied_invalid_status_raises(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:u3")
    aid = applied_service.mark_applied(test_db, jid)
    with pytest.raises(ValueError):
        applied_service.update_applied(test_db, aid, status="not_a_status")


def test_update_applied_nonexistent(test_db: sqlite3.Connection):
    assert applied_service.update_applied(test_db, 99999, status="offer") is False


def test_update_applied_no_fields(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:u4")
    aid = applied_service.mark_applied(test_db, jid)
    assert applied_service.update_applied(test_db, aid) is False


def test_update_applied_bumps_updated_at(test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:u5")
    aid = applied_service.mark_applied(test_db, jid)
    before = applied_service.get_applied(test_db, aid)["updated_at"]
    # Force a distinct timestamp window by setting an old updated_at.
    test_db.execute(
        "UPDATE applied_jobs SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (aid,)
    )
    test_db.commit()
    applied_service.update_applied(test_db, aid, status="phone_screen")
    after = applied_service.get_applied(test_db, aid)["updated_at"]
    assert after != "2000-01-01T00:00:00Z"
    assert before is not None
