from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import seed_job


def test_job_detail_returns_200(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Detail Role", description="Full job description here.")
    resp = client.get(f"/job/{jid}")
    assert resp.status_code == 200
    assert "Detail Role" in resp.text
    assert "Full job description here." in resp.text


def test_job_detail_marks_viewed(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, viewed_at=None)
    assert test_db.execute("SELECT viewed_at FROM jobs WHERE id=?", (jid,)).fetchone()["viewed_at"] is None
    client.get(f"/job/{jid}")
    # re-read via a fresh query (app committed on its own connection)
    row = test_db.execute("SELECT viewed_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["viewed_at"] is not None


def test_job_detail_404(client: TestClient):
    resp = client.get("/job/99999")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


def test_expire_action_removes_card(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db)
    resp = client.post(f"/job/{jid}/expire")
    assert resp.status_code == 200
    assert resp.text == ""  # empty body → HTMX removes the card
    row = test_db.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "expired"


def test_apply_action_moves_job(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:pageapply")
    resp = client.post(f"/job/{jid}/apply")
    assert resp.status_code == 200
    assert test_db.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone() is None
    assert test_db.execute(
        "SELECT id FROM applied_jobs WHERE global_id='gh:pageapply'"
    ).fetchone() is not None


def test_block_action_triggers_refresh(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, company="Block Inc")
    jid = seed_job(test_db, company="Block Inc")
    resp = client.post(f"/job/{jid}/block")
    assert resp.headers.get("HX-Refresh") == "true"
    # all jobs from that company are now blocked
    blocked = test_db.execute(
        "SELECT COUNT(*) c FROM jobs WHERE company='Block Inc' AND is_blocked=1"
    ).fetchone()["c"]
    assert blocked == 2
    assert test_db.execute(
        "SELECT id FROM company_blocklist WHERE company='Block Inc'"
    ).fetchone() is not None
