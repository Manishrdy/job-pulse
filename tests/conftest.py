from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jobhive.models import ATSType
from jobhive.models import Job as JobhiveJob

from jobpulse.app import create_app
from jobpulse.config import AppConfig, load_config
from jobpulse.database import init_db
from jobpulse.models import JobRecord


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def config_data() -> dict:
    return {
        "target_roles": ["Software Engineer", "Backend Engineer"],
        "ats_platforms": {
            "primary": ["greenhouse", "lever"],
            "secondary": ["smartrecruiters"],
            "low_priority": ["workday"],
        },
        "schedule": {"timezone": "US/Pacific"},
        "location": {"primary": "United States", "remote_preferred": True},
        "data_lifecycle": {"ttl_days": 3},
        "database": {"path": ""},
        "logging": {
            "level": "DEBUG",
            "file": "",
            "max_bytes": 10_485_760,
            "backup_count": 5,
        },
        "server": {"host": "127.0.0.1", "port": 8000},
    }


@pytest.fixture
def test_config(tmp_dir: Path, config_data: dict) -> AppConfig:
    db_path = tmp_dir / "test.db"
    log_path = tmp_dir / "logs" / "test.log"
    config_data["database"]["path"] = str(db_path)
    config_data["logging"]["file"] = str(log_path)
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(yaml.dump(config_data))
    return load_config(config_path)


@pytest.fixture
def test_db(test_config: AppConfig) -> sqlite3.Connection:
    conn = init_db(test_config)
    yield conn
    conn.close()


# --- Factories (Module 2+) -------------------------------------------------


def make_jobhive_job(**overrides) -> JobhiveJob:
    """Build a jobhive Job with sensible defaults; override any field."""
    base = {
        "url": "https://example.com/jobs/123",
        "title": "Software Engineer",
        "company": "acme",
        "ats_type": ATSType.GREENHOUSE,
        "ats_id": "123",
    }
    base.update(overrides)
    return JobhiveJob(**base)


def make_record(**overrides) -> JobRecord:
    """Build a JobRecord with sensible defaults; override any field."""
    base = {
        "global_id": "greenhouse:123",
        "url": "https://example.com/jobs/123",
        "title": "Software Engineer",
        "company": "Acme Corp",
        "ats_type": "greenhouse",
        "ats_id": "123",
    }
    base.update(overrides)
    return JobRecord(**base)


@pytest.fixture
def jobhive_job_factory():
    return make_jobhive_job


@pytest.fixture
def record_factory():
    return make_record


_seed_counter = itertools.count(1)


def seed_job(conn: sqlite3.Connection, **overrides) -> int:
    """Insert a row into ``jobs`` and return its id. Override any column."""
    n = next(_seed_counter)
    cols = {
        "global_id": f"gh:{n}",
        "url": "https://example.com/jobs/%d" % n,
        "title": "Software Engineer",
        "company": "Acme Corp",
        "ats_type": "greenhouse",
    }
    cols.update(overrides)
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO jobs ({names}) VALUES ({placeholders})", tuple(cols.values())
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def seed():
    return seed_job


@pytest.fixture
def client(test_config: AppConfig):
    app = create_app(test_config)
    with TestClient(app) as c:
        yield c
