from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from jobpulse.ingest import record_scrape_run
from jobpulse.services import applied_service
from tests.conftest import seed_job


def _apply(test_db: sqlite3.Connection, **job) -> int:
    """Seed a job and move it to applied_jobs; return applied id."""
    jid = seed_job(test_db, **job)
    return applied_service.mark_applied(test_db, jid)


# --- Applied page ----------------------------------------------------------


def test_applied_page_200_shows_jobs(client: TestClient, test_db: sqlite3.Connection):
    _apply(test_db, title="Backend Engineer", company="Acme", global_id="gh:ap1")
    resp = client.get("/applied")
    assert resp.status_code == 200
    assert "Backend Engineer" in resp.text
    assert "Acme" in resp.text
    assert "applied-table" in resp.text


def test_applied_empty_state(client: TestClient):
    html = client.get("/applied").text
    assert "No applied jobs yet" in html


def test_applied_status_dropdown_present(client: TestClient, test_db: sqlite3.Connection):
    aid = _apply(test_db, global_id="gh:ap2")
    html = client.get("/applied").text
    assert f'hx-post="/applied/{aid}/update"' in html
    # all pipeline statuses offered
    for value in ("applied", "phone_screen", "interview", "offer", "rejected", "ghosted"):
        assert f'value="{value}"' in html


def test_applied_status_update(client: TestClient, test_db: sqlite3.Connection):
    aid = _apply(test_db, global_id="gh:ap3")
    resp = client.post(
        f"/applied/{aid}/update",
        data={"status": "interview", "notes": "", "follow_up_date": ""},
    )
    assert resp.status_code == 200
    # returned row reflects new status as selected
    assert 'value="interview" selected' in resp.text
    row = applied_service.get_applied(test_db, aid)
    assert row["status"] == "interview"


def test_applied_notes_and_followup_save(client: TestClient, test_db: sqlite3.Connection):
    aid = _apply(test_db, global_id="gh:ap4")
    resp = client.post(
        f"/applied/{aid}/update",
        data={"status": "applied", "notes": "Recruiter called", "follow_up_date": "2026-07-15"},
    )
    assert resp.status_code == 200
    row = applied_service.get_applied(test_db, aid)
    assert row["notes"] == "Recruiter called"
    assert row["follow_up_date"] == "2026-07-15"


def test_applied_search_filter(client: TestClient, test_db: sqlite3.Connection):
    _apply(test_db, title="Backend Engineer", global_id="gh:ap5")
    _apply(test_db, title="Frontend Developer", global_id="gh:ap6")
    html = client.get("/applied", params={"search": "Backend"}).text
    assert "Backend Engineer" in html
    assert "Frontend Developer" not in html


def test_applied_status_filter(client: TestClient, test_db: sqlite3.Connection):
    aid = _apply(test_db, title="Offer Role", global_id="gh:ap7")
    applied_service.update_applied(test_db, aid, status="offer")
    _apply(test_db, title="Plain Role", global_id="gh:ap8")
    html = client.get("/applied", params={"status": "offer"}).text
    assert "Offer Role" in html
    assert "Plain Role" not in html


# --- Blocklist page --------------------------------------------------------


def test_blocklist_page_shows_companies(client: TestClient, test_db: sqlite3.Connection):
    from jobpulse.services import blocklist_service

    blocklist_service.add_company(test_db, "Evil Corp", reason="Rejected previously")
    resp = client.get("/blocklist")
    assert resp.status_code == 200
    assert "Evil Corp" in resp.text
    assert "Rejected previously" in resp.text


def test_blocklist_empty_state(client: TestClient):
    assert "No companies blocked" in client.get("/blocklist").text


def test_blocklist_add_via_form(client: TestClient, test_db: sqlite3.Connection):
    resp = client.post("/blocklist/add", data={"company": "Spammy Inc", "reason": "spam"})
    assert resp.headers.get("HX-Refresh") == "true"
    row = test_db.execute(
        "SELECT reason FROM company_blocklist WHERE company = 'Spammy Inc'"
    ).fetchone()
    assert row is not None
    assert row["reason"] == "spam"


def test_blocklist_unblock_action(client: TestClient, test_db: sqlite3.Connection):
    from jobpulse.services import blocklist_service

    jid = seed_job(test_db, company="Block Me")
    block_id = blocklist_service.add_company(test_db, "Block Me")
    assert test_db.execute("SELECT is_blocked FROM jobs WHERE id=?", (jid,)).fetchone()["is_blocked"] == 1

    resp = client.post(f"/blocklist/{block_id}/remove")
    assert resp.status_code == 200
    assert resp.text == ""  # row removed by empty swap
    assert test_db.execute(
        "SELECT id FROM company_blocklist WHERE id=?", (block_id,)
    ).fetchone() is None
    # jobs unhidden
    assert test_db.execute("SELECT is_blocked FROM jobs WHERE id=?", (jid,)).fetchone()["is_blocked"] == 0


# --- Scrape logs page ------------------------------------------------------


def test_scrape_logs_shows_runs(client: TestClient, test_db: sqlite3.Connection):
    record_scrape_run(
        test_db, schedule_slot="morning", ats_types_scraped="greenhouse,lever",
        jobs_fetched=120, jobs_inserted=40, jobs_updated=30, jobs_deleted=5,
        duration_seconds=12.4, status="success",
    )
    resp = client.get("/scrape-logs")
    assert resp.status_code == 200
    assert "greenhouse,lever" in resp.text
    assert "120" in resp.text
    assert "morning" in resp.text
    assert "success" in resp.text


def test_scrape_logs_empty_state(client: TestClient):
    assert "No scrape runs recorded" in client.get("/scrape-logs").text


def test_scrape_logs_shows_error(client: TestClient, test_db: sqlite3.Connection):
    record_scrape_run(
        test_db, schedule_slot="evening", ats_types_scraped="workday",
        jobs_fetched=0, jobs_inserted=0, jobs_updated=0, status="failure",
        error_msg="connection timeout",
    )
    html = client.get("/scrape-logs").text
    assert "failure" in html
    assert "connection timeout" in html
