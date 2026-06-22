from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import seed_job


def test_list_jobs_endpoint(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" in body and "total" in body
    assert body["total"] == 1


def test_list_jobs_filters_via_query(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, ats_type="greenhouse")
    seed_job(test_db, ats_type="lever")
    resp = client.get("/api/jobs", params={"ats": "lever"})
    assert resp.json()["total"] == 1


def test_list_jobs_invalid_sort_400(client: TestClient):
    resp = client.get("/api/jobs", params={"sort": "bogus"})
    assert resp.status_code == 400


def test_get_job_endpoint(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, title="Platform Engineer")
    resp = client.get(f"/api/jobs/{jid}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Platform Engineer"


def test_get_job_404(client: TestClient):
    assert client.get("/api/jobs/99999").status_code == 404


def test_expire_endpoint(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db)
    resp = client.post(f"/api/jobs/{jid}/expire")
    assert resp.status_code == 200
    assert resp.json()["expired"] is True


def test_viewed_endpoint(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db)
    resp = client.post(f"/api/jobs/{jid}/viewed")
    assert resp.json()["viewed"] is True


def test_apply_endpoint_and_404(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:apiapply")
    resp = client.post(f"/api/jobs/{jid}/apply")
    assert resp.status_code == 200
    assert "applied_id" in resp.json()
    # job is gone now
    assert client.post(f"/api/jobs/{jid}/apply").status_code == 404


def test_applied_list_and_patch(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, global_id="gh:apipatch")
    applied_id = client.post(f"/api/jobs/{jid}/apply").json()["applied_id"]

    resp = client.get("/api/applied")
    assert resp.json()["total"] == 1

    patch = client.patch(f"/api/applied/{applied_id}", json={"status": "interview", "notes": "hi"})
    assert patch.status_code == 200
    assert patch.json()["updated"] is True

    # invalid status -> 400
    bad = client.patch(f"/api/applied/{applied_id}", json={"status": "nope"})
    assert bad.status_code == 400


def test_applied_patch_404(client: TestClient):
    assert client.patch("/api/applied/99999", json={"status": "offer"}).status_code == 404


def test_blocklist_crud(client: TestClient, test_db: sqlite3.Connection):
    jid = seed_job(test_db, company="Bad Corp")

    add = client.post("/api/blocklist", json={"company": "Bad Corp", "reason": "x"})
    assert add.status_code == 200
    block_id = add.json()["id"]

    # job now blocked → excluded from feed
    assert client.get("/api/jobs").json()["total"] == 0

    listed = client.get("/api/blocklist").json()
    assert any(b["company"] == "Bad Corp" for b in listed)

    remove = client.delete(f"/api/blocklist/{block_id}")
    assert remove.status_code == 200
    # unhidden
    assert client.get("/api/jobs").json()["total"] == 1


def test_blocklist_add_empty_400(client: TestClient):
    assert client.post("/api/blocklist", json={"company": "  "}).status_code == 400


def test_blocklist_delete_404(client: TestClient):
    assert client.delete("/api/blocklist/99999").status_code == 404


def test_analytics_summary_endpoint(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    resp = client.get("/api/analytics/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "cards" in body
    assert body["cards"]["total_active_jobs"] == 1


def test_scrape_runs_endpoint(client: TestClient, test_db: sqlite3.Connection):
    from jobpulse.ingest import record_scrape_run

    record_scrape_run(
        test_db, schedule_slot="morning", ats_types_scraped="greenhouse",
        jobs_fetched=5, jobs_inserted=2, jobs_updated=1, status="success",
    )
    resp = client.get("/api/scrape-runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
