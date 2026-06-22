from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobpulse.config import load_config


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data))
    return p


BASE = {
    "target_roles": ["Software Engineer"],
    "ats_platforms": {"primary": ["greenhouse"]},
}


def test_cron_default_disabled(tmp_path: Path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.cron.enabled is False


def test_cron_enabled_from_yaml(tmp_path: Path):
    cfg = load_config(_write(tmp_path, {**BASE, "cron": {"enabled": True}}))
    assert cfg.cron.enabled is True


def test_cron_env_override_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOBPULSE_CRON_ENABLED", "true")
    cfg = load_config(_write(tmp_path, {**BASE, "cron": {"enabled": False}}))
    assert cfg.cron.enabled is True


def test_cron_env_override_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOBPULSE_CRON_ENABLED", "false")
    cfg = load_config(_write(tmp_path, {**BASE, "cron": {"enabled": True}}))
    assert cfg.cron.enabled is False


def test_cron_env_various_truthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for val in ("1", "yes", "on", "TRUE"):
        monkeypatch.setenv("JOBPULSE_CRON_ENABLED", val)
        assert load_config(_write(tmp_path, BASE)).cron.enabled is True


def test_cron_env_invalid_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOBPULSE_CRON_ENABLED", "maybe")
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, BASE))


def test_scrape_max_companies_default(tmp_path: Path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.scrape.max_companies_per_ats == 50


def test_scrape_max_companies_null(tmp_path: Path):
    cfg = load_config(_write(tmp_path, {**BASE, "scrape": {"max_companies_per_ats": None}}))
    assert cfg.scrape.max_companies_per_ats is None


def test_schedule_defaults_daily_5am_eastern(tmp_path: Path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.schedule.scrape_times == ["05:00"]
    assert cfg.schedule.cleanup_time == "02:00"
    assert cfg.schedule.timezone == "America/New_York"


def test_schedule_custom_times(tmp_path: Path):
    cfg = load_config(_write(tmp_path, {**BASE, "schedule": {"scrape_times": ["05:00", "17:30"]}}))
    assert cfg.schedule.scrape_times == ["05:00", "17:30"]


def test_schedule_empty_scrape_times_invalid(tmp_path: Path):
    with pytest.raises(Exception):
        load_config(_write(tmp_path, {**BASE, "schedule": {"scrape_times": []}}))


def test_schedule_bad_time_format_invalid(tmp_path: Path):
    with pytest.raises(Exception):
        load_config(_write(tmp_path, {**BASE, "schedule": {"scrape_times": ["5am"]}}))


def test_schedule_out_of_range_time_invalid(tmp_path: Path):
    with pytest.raises(Exception):
        load_config(_write(tmp_path, {**BASE, "schedule": {"scrape_times": ["25:00"]}}))


def test_real_config_yaml_valid():
    # The shipped config.yaml parses and validates.
    cfg = load_config("config.yaml")
    assert cfg.target_roles
    assert cfg.ats_platforms.primary
    assert cfg.schedule.scrape_times == ["05:00"]
    assert cfg.schedule.timezone == "America/New_York"
