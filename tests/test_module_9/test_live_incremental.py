from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpulse import pipeline
from jobpulse.config import AppConfig
from jobpulse.scraper import run_scrape
from tests.conftest import make_jobhive_job, seed_job


def _config(tmp_path: Path, primary: list[str]) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": primary},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
        scrape={"max_companies_per_ats": None, "concurrency": 4},
    )


# --- on_company callback (streaming) ---------------------------------------


def test_on_company_called_per_company(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\n" + "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(5)) + "\n"
    )
    config = _config(tmp_path, ["greenhouse"])
    calls = []

    def on_company(ats, fetched_count, records):
        calls.append((ats, fetched_count, len(records)))

    def fake(ats, ident):
        return [make_jobhive_job(title="Software Engineer", ats_id=f"{ident}-1")]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake, on_company=on_company)
    assert len(calls) == 5                      # one callback per company
    assert all(c == ("greenhouse", 1, 1) for c in calls)
    # With a callback, records are NOT retained on the result (ingested live).
    assert result.jobs == []
    assert result.total_fetched == 5


# --- Incremental ingest / crash safety -------------------------------------


def test_pipeline_ingests_incrementally(test_db: sqlite3.Connection, test_config, tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\n" + "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(6)) + "\n"
    )
    test_config.ats_platforms.primary[:] = ["greenhouse"]

    def fake(ats, ident):
        return [make_jobhive_job(title="Software Engineer", ats_id=f"{ident}")]

    pipeline.run_scrape_pipeline(
        test_config, scrape_fn=fake, manifest_dir=str(tmp_path)
    )
    # All 6 committed.
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 6


def test_partial_work_survives_midrun_crash(test_db: sqlite3.Connection, test_config, tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\n" + "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(5)) + "\n"
    )
    test_config.ats_platforms.primary[:] = ["greenhouse"]

    # Crash partway: ingest the first companies, then blow up.
    seen = {"n": 0}
    real_ingest = pipeline.ingest_jobs

    def exploding_ingest(conn, records, *, target_roles):
        seen["n"] += 1
        if seen["n"] > 2:
            raise RuntimeError("simulated crash")
        return real_ingest(conn, records, target_roles=target_roles)

    def fake(ats, ident):
        return [make_jobhive_job(title="Software Engineer", ats_id=ident)]

    import jobpulse.pipeline as plmod
    orig = plmod.ingest_jobs
    plmod.ingest_jobs = exploding_ingest
    try:
        with pytest.raises(RuntimeError):
            pipeline.run_scrape_pipeline(test_config, scrape_fn=fake, manifest_dir=str(tmp_path))
    finally:
        plmod.ingest_jobs = orig

    # The first 2 companies' jobs were committed before the crash.
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 2
    # And the failure was recorded.
    run = test_db.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "failure"


# --- Live progress state ---------------------------------------------------


def test_status_has_progress_shape():
    st = pipeline.get_status()
    assert "progress" in st
    for key in ("current_ats", "fetched", "inserted", "updated", "blocked", "per_ats"):
        assert key in st["progress"]


# --- Feed live poller ------------------------------------------------------


def test_feed_no_poller_when_idle(client: TestClient, test_db: sqlite3.Connection):
    seed_job(test_db, title="Software Engineer")
    html = client.get("/").text
    assert 'hx-trigger="every 4s"' not in html


def test_feed_poller_present_while_running(client: TestClient, test_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch):
    seed_job(test_db, title="Software Engineer")
    monkeypatch.setattr(pipeline, "is_running", lambda: True)
    html = client.get("/").text
    assert 'hx-trigger="every 4s"' in html
    assert "Scraping" in html


def test_partial_jobs_poller_while_running(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline, "is_running", lambda: True)
    html = client.get("/partials/jobs").text
    assert "/partials/jobs" in html and "every 4s" in html


# --- Scrape-logs live region -----------------------------------------------


def test_scrape_logs_partial_endpoint(client: TestClient):
    resp = client.get("/partials/scrape-logs")
    assert resp.status_code == 200
    assert "scrape-controls" in resp.text
    assert "<html" not in resp.text  # partial only


def test_scrape_logs_shows_poller_and_progress_while_running(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        pipeline, "get_status",
        lambda: {
            "running": True, "current": "scrape",
            "progress": {"current_ats": "greenhouse", "fetched": 120, "inserted": 8,
                         "updated": 3, "blocked": 1,
                         "per_ats": [{"ats_type": "greenhouse", "fetched": 120, "inserted": 8, "updated": 3, "blocked": 1, "errors": 0}]},
        },
    )
    html = client.get("/partials/scrape-logs").text
    assert "/partials/scrape-logs" in html and "every 2s" in html  # poller active
    assert "live-progress" in html
    assert "120" in html and "greenhouse" in html  # live counters