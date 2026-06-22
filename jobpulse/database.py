from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from jobpulse.config import AppConfig

log = logging.getLogger(__name__)

SCHEMA_SQL = """
-- Jobs table (live feed)
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id       TEXT    UNIQUE NOT NULL,
    url             TEXT    NOT NULL,
    apply_url       TEXT,
    title           TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    ats_type        TEXT    NOT NULL,
    ats_id          TEXT,
    location        TEXT,
    country_iso     TEXT,
    is_remote       INTEGER,
    salary_min      REAL,
    salary_max      REAL,
    salary_currency TEXT,
    salary_period   TEXT,
    salary_summary  TEXT,
    employment_type TEXT,
    department      TEXT,
    team            TEXT,
    experience      INTEGER,
    description     TEXT,
    posted_at       TIMESTAMP,
    first_seen      TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen       TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    viewed_at       TIMESTAMP,
    status          TEXT    NOT NULL DEFAULT 'active',
    expired_at      TIMESTAMP,
    is_blocked      INTEGER NOT NULL DEFAULT 0,
    relevance_score REAL    NOT NULL DEFAULT 0.0,
    language        TEXT,
    requisition_id  TEXT
);

-- Indexes on jobs
CREATE INDEX IF NOT EXISTS idx_jobs_global_id  ON jobs(global_id);
CREATE INDEX IF NOT EXISTS idx_jobs_company    ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at  ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_ats_type   ON jobs(ats_type);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title,
    company,
    description,
    location,
    content='jobs',
    content_rowid='id'
);

-- Applied jobs table (permanent)
CREATE TABLE IF NOT EXISTS applied_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id       TEXT    UNIQUE NOT NULL,
    url             TEXT    NOT NULL,
    apply_url       TEXT,
    title           TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    ats_type        TEXT    NOT NULL,
    location        TEXT,
    is_remote       INTEGER,
    salary_summary  TEXT,
    employment_type TEXT,
    description     TEXT,
    posted_at       TIMESTAMP,
    first_seen      TIMESTAMP NOT NULL,
    applied_at      TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status          TEXT    NOT NULL DEFAULT 'applied',
    notes           TEXT,
    follow_up_date  DATE,
    updated_at      TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Indexes on applied_jobs
CREATE INDEX IF NOT EXISTS idx_applied_global_id  ON applied_jobs(global_id);
CREATE INDEX IF NOT EXISTS idx_applied_status     ON applied_jobs(status);
CREATE INDEX IF NOT EXISTS idx_applied_applied_at ON applied_jobs(applied_at);
CREATE INDEX IF NOT EXISTS idx_applied_company    ON applied_jobs(company);

-- Company blocklist
CREATE TABLE IF NOT EXISTS company_blocklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company    TEXT    UNIQUE NOT NULL,
    blocked_at TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reason     TEXT
);

-- Scrape runs (audit log)
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at            TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    schedule_slot     TEXT,
    ats_types_scraped TEXT    NOT NULL,
    jobs_fetched      INTEGER NOT NULL DEFAULT 0,
    jobs_inserted     INTEGER NOT NULL DEFAULT 0,
    jobs_updated      INTEGER NOT NULL DEFAULT 0,
    jobs_deleted      INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL,
    status            TEXT    NOT NULL,
    error_msg         TEXT
);
"""

FTS_TRIGGERS_SQL = """
-- Keep FTS5 in sync with jobs table

CREATE TRIGGER IF NOT EXISTS jobs_ai AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts(rowid, title, company, description, location)
    VALUES (new.id, new.title, new.company, new.description, new.location);
END;

CREATE TRIGGER IF NOT EXISTS jobs_ad AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, company, description, location)
    VALUES ('delete', old.id, old.title, old.company, old.description, old.location);
END;

CREATE TRIGGER IF NOT EXISTS jobs_au AFTER UPDATE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, company, description, location)
    VALUES ('delete', old.id, old.title, old.company, old.description, old.location);
    INSERT INTO jobs_fts(rowid, title, company, description, location)
    VALUES (new.id, new.title, new.company, new.description, new.location);
END;
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.Connection(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(config: AppConfig) -> sqlite3.Connection:
    db_path = Path(config.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS_TRIGGERS_SQL)
    conn.commit()
    log.info("Database initialized at %s", db_path)
    return conn
