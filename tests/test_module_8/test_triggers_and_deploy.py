from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpulse import pipeline
from jobpulse.app import create_app
from jobpulse.config import AppConfig

ROOT = Path(__file__).resolve().parent.parent.parent


# --- UI trigger routes -----------------------------------------------------


def test_scrape_logs_shows_controls_disabled(client: TestClient):
    html = client.get("/scrape-logs").text
    assert "Run scrape now" in html
    assert "Run cleanup now" in html
    assert "disabled (manual)" in html  # test_config has cron disabled


def test_scrape_run_trigger(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        pipeline, "run_scrape_in_background", lambda config, **kw: calls.append(kw) or True
    )
    resp = client.post("/scrape/run")
    assert resp.status_code == 200
    assert calls == [{"schedule_slot": "manual"}]
    # Returns the live region (so the page starts polling without a full refresh).
    assert "scrape-controls" in resp.text


def test_cleanup_run_trigger(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        pipeline, "run_cleanup_in_background", lambda config: calls.append(True) or True
    )
    resp = client.post("/cleanup/run")
    assert resp.status_code == 200
    assert calls == [True]
    assert "scrape-controls" in resp.text


# --- App scheduler wiring --------------------------------------------------


def _cfg(tmp_path: Path, cron_enabled: bool) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        database={"path": str(tmp_path / "app.db")},
        logging={"file": str(tmp_path / "app.log")},
        cron={"enabled": cron_enabled},
    )


def test_app_starts_scheduler_when_cron_enabled(tmp_path: Path):
    app = create_app(_cfg(tmp_path, cron_enabled=True))
    with TestClient(app):
        assert app.state.scheduler is not None


def test_app_no_scheduler_when_cron_disabled(tmp_path: Path):
    app = create_app(_cfg(tmp_path, cron_enabled=False))
    with TestClient(app):
        assert app.state.scheduler is None


# --- Deployment artifacts --------------------------------------------------


def test_deployment_files_exist():
    assert (ROOT / "scripts" / "run_scrape.py").exists()
    assert (ROOT / "scripts" / "run_cleanup.py").exists()
    assert (ROOT / "scripts" / "crontab.example").exists()
    assert (ROOT / "systemd" / "jobpulse.service").exists()
    assert (ROOT / "LICENSE").exists()
    assert (ROOT / ".env.example").exists()


def test_license_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "jobhive" in text  # attribution preserved


def test_readme_credits_jobhive():
    text = (ROOT / "README.md").read_text()
    assert "jobhive" in text
    assert "MIT" in text


def test_systemd_unit_uses_env_file():
    text = (ROOT / "systemd" / "jobpulse.service").read_text()
    assert "EnvironmentFile" in text
    assert "create_app" in text
