"""Data lifecycle: TTL-based deletion and the per-job expire action.

Two concerns (SCOPE §FR-06):

- **TTL delete** (FR-06.1/6.4): every row in ``jobs`` whose ``first_seen``
  is older than ``ttl_days`` is hard-deleted, regardless of ``status`` —
  so expired jobs are still reaped on schedule. The ``applied_jobs`` table
  is a separate table and is never touched (FR-06.2).
- **Expire** (FR-06.3): a user action that flags a single job
  ``status='expired'`` / ``expired_at=now`` so it drops out of the default
  feed while remaining subject to the TTL above.

The TTL cutoff is computed from an injectable ``now`` so the day-boundary
behavior is deterministic in tests.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from jobpulse.config import AppConfig
from jobpulse.ingest import record_scrape_run

log = logging.getLogger(__name__)

# Storage format for first_seen/last_seen — matches the DB column DEFAULT
# (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')). Fixed-width and zero-padded, so
# lexicographic string comparison is equivalent to chronological order.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"


def _format_cutoff(now: datetime, ttl_days: int) -> str:
    cutoff = now.astimezone(UTC) - timedelta(days=ttl_days)
    return cutoff.strftime(_TS_FORMAT)


def cleanup_old_jobs(
    conn: sqlite3.Connection,
    ttl_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """Delete jobs whose ``first_seen`` is strictly older than the TTL cutoff.

    A job exactly ``ttl_days`` old is **kept** (the comparison is strict
    ``<`` against ``now - ttl_days``); one second past that is deleted.
    Returns the number of rows deleted. The FTS index is kept in sync by
    the ON DELETE trigger.
    """
    reference = now if now is not None else datetime.now(UTC)
    cutoff = _format_cutoff(reference, ttl_days)

    cursor = conn.execute("DELETE FROM jobs WHERE first_seen < ?", (cutoff,))
    conn.commit()
    deleted = cursor.rowcount
    log.info("TTL cleanup deleted %d jobs older than %s (ttl=%dd)", deleted, cutoff, ttl_days)
    return deleted


def run_cleanup(
    conn: sqlite3.Connection,
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> int:
    """Run the TTL delete and record the result to ``scrape_runs`` (FR-06.1).

    Returns the deleted count.
    """
    deleted = cleanup_old_jobs(conn, config.data_lifecycle.ttl_days, now=now)
    record_scrape_run(
        conn,
        schedule_slot="cleanup",
        ats_types_scraped="",
        jobs_fetched=0,
        jobs_inserted=0,
        jobs_updated=0,
        jobs_deleted=deleted,
        status="success",
    )
    return deleted


def expire_job(conn: sqlite3.Connection, job_id: int) -> bool:
    """Mark a single job expired (FR-06.3).

    Sets ``status='expired'`` and ``expired_at=now`` for an active job.
    Returns True if a row was updated, False if the job doesn't exist or
    was already expired.
    """
    cursor = conn.execute(
        f"UPDATE jobs SET status = 'expired', expired_at = {_NOW_SQL} "
        f"WHERE id = ? AND status != 'expired'",
        (job_id,),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    if changed:
        log.info("Job %d marked expired", job_id)
    else:
        log.warning("Expire no-op for job %d (missing or already expired)", job_id)
    return changed
