from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobpulse.config import AppConfig, load_config


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_load_config_with_defaults(test_config: AppConfig):
    assert test_config.target_roles == ["Software Engineer", "Backend Engineer"]
    assert test_config.ats_platforms.primary == ["greenhouse", "lever"]
    assert test_config.data_lifecycle.ttl_days == 3
    assert test_config.location.primary == "United States"
    assert test_config.location.remote_preferred is True
    assert test_config.schedule.timezone == "US/Pacific"
    assert test_config.logging.max_bytes == 10_485_760
    assert test_config.logging.backup_count == 5
    assert test_config.server.port == 8000


def test_config_all_platforms(test_config: AppConfig):
    all_plats = test_config.ats_platforms.all_platforms
    assert all_plats == ["greenhouse", "lever", "smartrecruiters", "workday"]


def test_config_missing_target_roles(tmp_path: Path):
    data = {
        "ats_platforms": {"primary": ["greenhouse"]},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_empty_target_roles(tmp_path: Path):
    data = {
        "target_roles": [],
        "ats_platforms": {"primary": ["greenhouse"]},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_empty_string_roles_rejected(tmp_path: Path):
    data = {
        "target_roles": ["", "  "],
        "ats_platforms": {"primary": ["greenhouse"]},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_missing_ats_primary(tmp_path: Path):
    data = {
        "target_roles": ["Software Engineer"],
        "ats_platforms": {"primary": [], "secondary": ["lever"]},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_config_invalid_yaml(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("not: a: valid: - yaml: [")
    with pytest.raises(Exception):
        load_config(p)


def test_config_not_a_mapping(tmp_path: Path):
    p = tmp_path / "list.yaml"
    p.write_text("- item1\n- item2\n")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_config(p)


def test_config_invalid_log_level(tmp_path: Path):
    data = {
        "target_roles": ["Software Engineer"],
        "ats_platforms": {"primary": ["greenhouse"]},
        "logging": {"level": "INVALID"},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_ttl_days_minimum(tmp_path: Path):
    data = {
        "target_roles": ["Software Engineer"],
        "ats_platforms": {"primary": ["greenhouse"]},
        "data_lifecycle": {"ttl_days": 0},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_port_bounds(tmp_path: Path):
    data = {
        "target_roles": ["Software Engineer"],
        "ats_platforms": {"primary": ["greenhouse"]},
        "server": {"port": 99999},
    }
    with pytest.raises(Exception):
        load_config(_write_config(tmp_path, data))


def test_config_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = {
        "target_roles": ["AI Engineer"],
        "ats_platforms": {"primary": ["ashby"]},
    }
    p = _write_config(tmp_path, data)
    monkeypatch.setenv("JOBPULSE_CONFIG", str(p))
    config = load_config()
    assert config.target_roles == ["AI Engineer"]
    assert config.ats_platforms.primary == ["ashby"]
