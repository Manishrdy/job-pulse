from __future__ import annotations

import sqlite3
from pathlib import Path

from jobpulse import pipeline
from jobpulse.config import AppConfig
from tests.conftest import make_jobhive_job


def _config(tmp_path: Path, manifest_dir: Path) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        database={"path": str(tmp_path / "pipe.db")},
        logging={"file": str(tmp_path / "pipe.log")},
        scrape={"max_companies_per_ats": None},
    )


def _manifest(tmp_path: Path) -> Path:
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nAcme,acme,https://e.com/acme\nBeta,beta,https://e.com/beta\n"
    )
    return tmp_path


def test_scrape_pipeline_end_to_end(test_db: sqlite3.Connection, test_config, tmp_path: Path):
    # Use the shared test_config DB so we can inspect results via test_db.
    manifest = _manifest(tmp_path)

    def fake_scrape(ats, identifier):
        return [
            make_jobhive_job(title="Software Engineer", ats_id=f"{identifier}-1"),
            make_jobhive_job(title="Marketing Manager", ats_id=f"{identifier}-2"),
        ]

    result = pipeline.run_scrape_pipeline(
        test_config, schedule_slot="morning", scrape_fn=fake_scrape, manifest_dir=str(manifest)
    )

    assert result["status"] == "success"
    assert result["fetched"] == 4      # 2 companies x 2 jobs
    assert result["inserted"] == 2     # only the matching titles
    # jobs landed in the DB
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 2
    # run recorded
    run = test_db.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["schedule_slot"] == "morning"
    assert run["jobs_fetched"] == 4
    assert run["status"] == "success"


def test_scrape_pipeline_skips_when_locked(test_config, tmp_path: Path):
    manifest = _manifest(tmp_path)
    # Hold the lock to simulate a run in progress.
    assert pipeline._pipeline_lock.acquire(blocking=False)
    try:
        result = pipeline.run_scrape_pipeline(
            test_config, scrape_fn=lambda a, i: [], manifest_dir=str(manifest)
        )
        assert result["status"] == "skipped"
    finally:
        pipeline._pipeline_lock.release()


def test_cleanup_pipeline(test_db: sqlite3.Connection, test_config):
    # Seed an old job that should be deleted.
    test_db.execute(
        "INSERT INTO jobs (global_id, url, title, company, ats_type, first_seen, last_seen) "
        "VALUES ('gh:old', 'https://e.com', 'SWE', 'Acme', 'greenhouse', "
        "'2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z')"
    )
    test_db.commit()

    result = pipeline.run_cleanup_pipeline(test_config)
    assert result["status"] == "success"
    assert result["deleted"] == 1
    assert test_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0
    run = test_db.execute(
        "SELECT * FROM scrape_runs WHERE schedule_slot='cleanup' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["jobs_deleted"] == 1


def test_get_status_shape():
    status = pipeline.get_status()
    assert "running" in status
    assert "last_scrape" in status
    assert "last_cleanup" in status
