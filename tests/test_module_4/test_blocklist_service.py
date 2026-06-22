from __future__ import annotations

import sqlite3

import pytest

from jobpulse.services import blocklist_service, jobs_service
from tests.conftest import seed_job


def test_add_company_hides_existing_jobs(test_db: sqlite3.Connection):
    jid = seed_job(test_db, company="Bad Corp")
    other = seed_job(test_db, company="Good Corp")

    blocklist_service.add_company(test_db, "Bad Corp", reason="Rejected previously")

    # Bad Corp job is now flagged and excluded from the default feed.
    job = jobs_service.get_job(test_db, jid)
    assert job["is_blocked"] == 1
    ids = {j["id"] for j in jobs_service.list_jobs(test_db)["jobs"]}
    assert ids == {other}


def test_remove_company_unhides_jobs(test_db: sqlite3.Connection):
    jid = seed_job(test_db, company="Bad Corp")
    block_id = blocklist_service.add_company(test_db, "Bad Corp")
    assert jobs_service.get_job(test_db, jid)["is_blocked"] == 1

    assert blocklist_service.remove_company(test_db, block_id) is True
    job = jobs_service.get_job(test_db, jid)
    assert job["is_blocked"] == 0
    ids = {j["id"] for j in jobs_service.list_jobs(test_db)["jobs"]}
    assert jid in ids


def test_add_company_idempotent(test_db: sqlite3.Connection):
    first = blocklist_service.add_company(test_db, "Dup Corp")
    second = blocklist_service.add_company(test_db, "Dup Corp", reason="again")
    assert first == second
    rows = blocklist_service.list_blocked(test_db)
    assert len([r for r in rows if r["company"] == "Dup Corp"]) == 1


def test_add_company_empty_raises(test_db: sqlite3.Connection):
    with pytest.raises(ValueError):
        blocklist_service.add_company(test_db, "   ")


def test_remove_nonexistent(test_db: sqlite3.Connection):
    assert blocklist_service.remove_company(test_db, 99999) is False


def test_list_blocked(test_db: sqlite3.Connection):
    blocklist_service.add_company(test_db, "A Corp", reason="r1")
    blocklist_service.add_company(test_db, "B Corp")
    rows = blocklist_service.list_blocked(test_db)
    companies = {r["company"] for r in rows}
    assert companies == {"A Corp", "B Corp"}


def test_block_applies_to_future_company_jobs_via_ingest(test_db: sqlite3.Connection):
    # Blocking a company, then a later job from that company is flagged by ingest.
    from jobpulse.ingest import ingest_jobs
    from tests.conftest import make_record

    blocklist_service.add_company(test_db, "Future Bad")
    rec = make_record(global_id="gh:futureblock", company="Future Bad")
    ingest_jobs(test_db, [rec], target_roles=["Software Engineer"])
    row = test_db.execute(
        "SELECT is_blocked FROM jobs WHERE global_id = 'gh:futureblock'"
    ).fetchone()
    assert row["is_blocked"] == 1
