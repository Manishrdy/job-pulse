from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from jobpulse.ingest import record_scrape_run
from jobpulse.services import analytics_service, applied_service
from tests.conftest import seed_job

REF = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
ROLES = ["Software Engineer", "Backend Engineer", "AI Engineer"]


def test_role_breakdown(test_db: sqlite3.Connection):
    seed_job(test_db, title="Senior Software Engineer")
    seed_job(test_db, title="Backend Engineer")
    seed_job(test_db, title="Backend Engineer II")
    seed_job(test_db, title="Marketing Manager")

    breakdown = {r["role"]: r for r in analytics_service.role_breakdown(test_db, ROLES)}
    assert breakdown["Software Engineer"]["seen"] == 1
    assert breakdown["Backend Engineer"]["seen"] == 2
    assert breakdown["AI Engineer"]["seen"] == 0
    assert breakdown["Software Engineer"]["applied"] == 0


def test_role_breakdown_counts_applied(test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Backend Engineer", global_id="gh:rb1")
    applied_service.mark_applied(test_db, jid)
    breakdown = {r["role"]: r for r in analytics_service.role_breakdown(test_db, ROLES)}
    assert breakdown["Backend Engineer"]["applied"] == 1
    # moved out of jobs, so seen drops to 0
    assert breakdown["Backend Engineer"]["seen"] == 0


def test_applications_per_day(test_db: sqlite3.Connection):
    for i in range(2):
        jid = seed_job(test_db, global_id=f"gh:d{i}")
        applied_service.mark_applied(test_db, jid)
    rows = analytics_service.applications_per_day(test_db)
    assert sum(r["count"] for r in rows) == 2


def test_ats_breakdown(test_db: sqlite3.Connection):
    seed_job(test_db, ats_type="greenhouse")
    seed_job(test_db, ats_type="greenhouse")
    seed_job(test_db, ats_type="lever")
    jid = seed_job(test_db, ats_type="ashby", global_id="gh:ats1")
    applied_service.mark_applied(test_db, jid)

    result = analytics_service.ats_breakdown(test_db)
    jobs_counts = {r["ats_type"]: r["count"] for r in result["jobs"]}
    assert jobs_counts["greenhouse"] == 2
    assert jobs_counts["lever"] == 1
    applied_counts = {r["ats_type"]: r["count"] for r in result["applied"]}
    assert applied_counts["ashby"] == 1


def test_status_funnel(test_db: sqlite3.Connection):
    jid1 = seed_job(test_db, global_id="gh:f1")
    a1 = applied_service.mark_applied(test_db, jid1)
    applied_service.update_applied(test_db, a1, status="interview")
    jid2 = seed_job(test_db, global_id="gh:f2")
    applied_service.mark_applied(test_db, jid2)  # stays 'applied'

    funnel = {r["status"]: r["count"] for r in analytics_service.status_funnel(test_db)}
    assert funnel["interview"] == 1
    assert funnel["applied"] == 1


def test_scrape_trends(test_db: sqlite3.Connection):
    record_scrape_run(
        test_db, schedule_slot="morning", ats_types_scraped="greenhouse",
        jobs_fetched=10, jobs_inserted=5, jobs_updated=0, status="success",
    )
    record_scrape_run(
        test_db, schedule_slot="evening", ats_types_scraped="lever",
        jobs_fetched=8, jobs_inserted=3, jobs_updated=0, status="success",
    )
    trends = analytics_service.scrape_trends(test_db)
    # both runs are "today" → one grouped row summing inserted
    assert sum(r["inserted"] for r in trends) == 8


def test_summary_cards(test_db: sqlite3.Connection):
    # 2 active jobs
    seed_job(test_db)
    seed_job(test_db)
    # 3 applied, one with a response
    for i in range(3):
        jid = seed_job(test_db, global_id=f"gh:sum{i}")
        aid = applied_service.mark_applied(test_db, jid)
        if i == 0:
            applied_service.update_applied(test_db, aid, status="interview")

    summary = analytics_service.summary(test_db, ROLES, now=REF)
    cards = summary["cards"]
    assert cards["total_active_jobs"] == 2
    assert cards["total_applied"] == 3
    assert cards["applications_this_week"] == 3
    assert cards["response_rate"] == round(1 / 3, 4)
    # bundled sections present
    assert "role_breakdown" in summary
    assert "applications_per_day" in summary
    assert "ats_breakdown" in summary
    assert "status_funnel" in summary
    assert "scrape_trends" in summary


def test_summary_empty_state(test_db: sqlite3.Connection):
    summary = analytics_service.summary(test_db, ROLES, now=REF)
    assert summary["cards"]["total_applied"] == 0
    assert summary["cards"]["response_rate"] == 0.0
