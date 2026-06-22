from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from jobpulse.services import jobs_service
from tests.conftest import seed_job

REF = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def test_default_sort_posted_desc_nulls_last_then_first_seen(test_db: sqlite3.Connection):
    j_old = seed_job(test_db, posted_at="2026-06-10T00:00:00Z", first_seen="2026-06-10T00:00:00Z")
    j_new = seed_job(test_db, posted_at="2026-06-20T00:00:00Z", first_seen="2026-06-20T00:00:00Z")
    j_null_early = seed_job(test_db, posted_at=None, first_seen="2026-06-21T00:00:00Z")
    j_null_late = seed_job(test_db, posted_at=None, first_seen="2026-06-22T00:00:00Z")

    result = jobs_service.list_jobs(test_db)
    ids = [j["id"] for j in result["jobs"]]
    # posted desc first, then nulls ordered by first_seen desc
    assert ids == [j_new, j_old, j_null_late, j_null_early]


def test_blocked_excluded_by_default(test_db: sqlite3.Connection):
    visible = seed_job(test_db)
    seed_job(test_db, is_blocked=1)
    result = jobs_service.list_jobs(test_db)
    ids = [j["id"] for j in result["jobs"]]
    assert ids == [visible]


def test_expired_excluded_by_default(test_db: sqlite3.Connection):
    visible = seed_job(test_db)
    seed_job(test_db, status="expired")
    result = jobs_service.list_jobs(test_db)
    ids = [j["id"] for j in result["jobs"]]
    assert ids == [visible]


def test_include_blocked_and_expired(test_db: sqlite3.Connection):
    seed_job(test_db)
    seed_job(test_db, is_blocked=1)
    seed_job(test_db, status="expired")
    result = jobs_service.list_jobs(test_db, include_blocked=True, include_expired=True)
    assert result["total"] == 3


def test_filter_by_ats(test_db: sqlite3.Connection):
    seed_job(test_db, ats_type="greenhouse")
    lever = seed_job(test_db, ats_type="lever")
    result = jobs_service.list_jobs(test_db, ats="lever")
    assert [j["id"] for j in result["jobs"]] == [lever]


def test_filter_by_role_title(test_db: sqlite3.Connection):
    swe = seed_job(test_db, title="Senior Software Engineer")
    seed_job(test_db, title="Marketing Manager")
    result = jobs_service.list_jobs(test_db, role="Software Engineer")
    assert [j["id"] for j in result["jobs"]] == [swe]


def test_filter_by_location(test_db: sqlite3.Connection):
    sf = seed_job(test_db, location="San Francisco, CA")
    seed_job(test_db, location="New York, NY")
    result = jobs_service.list_jobs(test_db, location="San Francisco")
    assert [j["id"] for j in result["jobs"]] == [sf]


def test_filter_remote_only(test_db: sqlite3.Connection):
    remote = seed_job(test_db, is_remote=1)
    seed_job(test_db, is_remote=0)
    seed_job(test_db, is_remote=None)
    result = jobs_service.list_jobs(test_db, remote_only=True)
    assert [j["id"] for j in result["jobs"]] == [remote]


def test_filter_employment_type(test_db: sqlite3.Connection):
    ft = seed_job(test_db, employment_type="FULL_TIME")
    seed_job(test_db, employment_type="CONTRACT")
    result = jobs_service.list_jobs(test_db, employment_type="FULL_TIME")
    assert [j["id"] for j in result["jobs"]] == [ft]


def test_filter_salary_min(test_db: sqlite3.Connection):
    high = seed_job(test_db, salary_min=180000.0)
    seed_job(test_db, salary_min=90000.0)
    seed_job(test_db, salary_min=None)  # excluded when filter set
    result = jobs_service.list_jobs(test_db, salary_min=150000.0)
    assert [j["id"] for j in result["jobs"]] == [high]


def test_filter_posted_within_days(test_db: sqlite3.Connection):
    recent = seed_job(test_db, posted_at="2026-06-21T00:00:00Z")
    seed_job(test_db, posted_at="2026-06-15T00:00:00Z")  # older than 2 days
    # null posted_at falls back to first_seen
    recent_fs = seed_job(test_db, posted_at=None, first_seen="2026-06-22T00:00:00Z")
    result = jobs_service.list_jobs(test_db, posted_within_days=2, now=REF)
    ids = {j["id"] for j in result["jobs"]}
    assert ids == {recent, recent_fs}


def test_combined_filters_intersection(test_db: sqlite3.Connection):
    target = seed_job(
        test_db, title="Backend Engineer", ats_type="lever", is_remote=1, salary_min=170000.0
    )
    seed_job(test_db, title="Backend Engineer", ats_type="greenhouse", is_remote=1)
    seed_job(test_db, title="Backend Engineer", ats_type="lever", is_remote=0)
    result = jobs_service.list_jobs(
        test_db, role="Backend Engineer", ats="lever", remote_only=True, salary_min=150000.0
    )
    assert [j["id"] for j in result["jobs"]] == [target]


def test_pagination(test_db: sqlite3.Connection):
    for _ in range(5):
        seed_job(test_db, first_seen="2026-06-20T00:00:00Z")
    page1 = jobs_service.list_jobs(test_db, limit=2, offset=0)
    page2 = jobs_service.list_jobs(test_db, limit=2, offset=2)
    page3 = jobs_service.list_jobs(test_db, limit=2, offset=4)
    assert page1["total"] == 5
    assert len(page1["jobs"]) == 2
    assert len(page2["jobs"]) == 2
    assert len(page3["jobs"]) == 1
    # no overlap
    seen = {j["id"] for j in page1["jobs"]} | {j["id"] for j in page2["jobs"]}
    assert len(seen) == 4


def test_fts_search_returns_ranked_results(test_db: sqlite3.Connection):
    title_hit = seed_job(test_db, title="Backend Engineer", description="ordinary role")
    desc_hit = seed_job(test_db, title="Office Coordinator", description="we want a backend engineer")
    seed_job(test_db, title="Marketing Manager", description="brand work")
    result = jobs_service.list_jobs(test_db, search="backend engineer")
    ids = [j["id"] for j in result["jobs"]]
    assert ids == [title_hit, desc_hit]  # title match ranks first


def test_sort_relevance(test_db: sqlite3.Connection):
    low = seed_job(test_db, relevance_score=1.0, first_seen="2026-06-20T00:00:00Z")
    high = seed_job(test_db, relevance_score=9.0, first_seen="2026-06-20T00:00:00Z")
    result = jobs_service.list_jobs(test_db, sort="relevance")
    assert [j["id"] for j in result["jobs"]] == [high, low]


def test_sort_salary(test_db: sqlite3.Connection):
    low = seed_job(test_db, salary_max=120000.0)
    high = seed_job(test_db, salary_max=200000.0)
    seed_job(test_db, salary_max=None)  # nulls last
    result = jobs_service.list_jobs(test_db, sort="salary")
    ids = [j["id"] for j in result["jobs"]]
    assert ids[0] == high
    assert ids[1] == low


def test_invalid_sort_raises(test_db: sqlite3.Connection):
    import pytest

    with pytest.raises(ValueError):
        jobs_service.list_jobs(test_db, sort="bogus")


def test_get_job(test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Platform Engineer")
    job = jobs_service.get_job(test_db, jid)
    assert job is not None
    assert job["title"] == "Platform Engineer"
    assert jobs_service.get_job(test_db, 99999) is None


def test_mark_viewed(test_db: sqlite3.Connection):
    jid = seed_job(test_db)
    assert jobs_service.mark_viewed(test_db, jid) is True
    job = jobs_service.get_job(test_db, jid)
    assert job["viewed_at"] is not None
    # second call is a no-op (already viewed)
    assert jobs_service.mark_viewed(test_db, jid) is False


def test_expire_job_via_service(test_db: sqlite3.Connection):
    jid = seed_job(test_db)
    assert jobs_service.expire_job(test_db, jid) is True
    job = jobs_service.get_job(test_db, jid)
    assert job["status"] == "expired"
    # excluded from default feed now
    result = jobs_service.list_jobs(test_db)
    assert jid not in {j["id"] for j in result["jobs"]}
