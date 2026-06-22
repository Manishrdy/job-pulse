"""Ingestion pipeline: dedup, insert/update, blocklist, scrape-run logging.

Takes the mapped :class:`~jobpulse.models.JobRecord` list produced by the
scraper and reconciles it against the ``jobs`` table:

- **New** ``global_id`` → INSERT with ``first_seen``/``last_seen`` = now,
  then compute and store the FTS5 relevance score.
- **Existing** ``global_id`` → bump ``last_seen`` (and refresh
  ``is_blocked``), no duplicate row (FR-01.5).
- Companies present in ``company_blocklist`` get ``is_blocked = 1`` at
  ingest time — jobs are still stored, just flagged (FR-01.8).

Every scrape run is recorded to ``scrape_runs`` (FR-01.7).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from jobpulse.models import INSERT_COLUMNS, JobRecord
from jobpulse.scoring import build_match_query, compute_relevance

log = logging.getLogger(__name__)

_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

_INSERT_SQL = (
    f"INSERT INTO jobs ({', '.join(INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})"
)


@dataclass
class IngestStats:
    """Counts from a single ingest pass."""

    total: int = 0
    inserted: int = 0
    updated: int = 0
    blocked: int = 0


def load_blocklist(conn: sqlite3.Connection) -> set[str]:
    """Return the set of blocked company names (exact match, per FR-05.1)."""
    rows = conn.execute("SELECT company FROM company_blocklist").fetchall()
    return {row["company"] for row in rows}


def ingest_jobs(
    conn: sqlite3.Connection,
    jobs: list[JobRecord],
    *,
    target_roles: list[str],
) -> IngestStats:
    """Reconcile scraped jobs into the ``jobs`` table.

    Dedups on ``global_id``, applies the blocklist, and computes a
    relevance score for newly inserted rows. Commits once at the end.
    """
    stats = IngestStats(total=len(jobs))
    blocklist = load_blocklist(conn)
    match_query = build_match_query(target_roles)

    for job in jobs:
        is_blocked = 1 if job.company in blocklist else 0

        existing = conn.execute(
            "SELECT id FROM jobs WHERE global_id = ?", (job.global_id,)
        ).fetchone()

        if existing is not None:
            conn.execute(
                f"UPDATE jobs SET last_seen = {_NOW_SQL}, is_blocked = ? "
                f"WHERE global_id = ?",
                (is_blocked, job.global_id),
            )
            stats.updated += 1
            continue

        # New job — insert, then score it. The INSERT trigger populates
        # jobs_fts synchronously so bm25 sees the row in this transaction.
        values = list(job.insert_values())
        values[INSERT_COLUMNS.index("is_blocked")] = is_blocked
        cursor = conn.execute(_INSERT_SQL, tuple(values))
        rowid = cursor.lastrowid

        relevance = compute_relevance(conn, rowid, match_query)
        if relevance:
            conn.execute(
                "UPDATE jobs SET relevance_score = ? WHERE id = ?",
                (relevance, rowid),
            )

        stats.inserted += 1
        if is_blocked:
            stats.blocked += 1

    conn.commit()
    log.info(
        "Ingest complete: %d total, %d inserted, %d updated, %d blocked",
        stats.total,
        stats.inserted,
        stats.updated,
        stats.blocked,
    )
    return stats


def record_scrape_run(
    conn: sqlite3.Connection,
    *,
    schedule_slot: str | None,
    ats_types_scraped: list[str] | str,
    jobs_fetched: int,
    jobs_inserted: int,
    jobs_updated: int,
    jobs_deleted: int = 0,
    duration_seconds: float | None = None,
    status: str,
    error_msg: str | None = None,
) -> int:
    """Insert a row into ``scrape_runs`` (FR-01.7). Returns the new row id."""
    ats_str = (
        ",".join(ats_types_scraped)
        if isinstance(ats_types_scraped, list)
        else ats_types_scraped
    )
    cursor = conn.execute(
        """
        INSERT INTO scrape_runs (
            schedule_slot, ats_types_scraped, jobs_fetched, jobs_inserted,
            jobs_updated, jobs_deleted, duration_seconds, status, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schedule_slot,
            ats_str,
            jobs_fetched,
            jobs_inserted,
            jobs_updated,
            jobs_deleted,
            duration_seconds,
            status,
            error_msg,
        ),
    )
    conn.commit()
    log.info(
        "Scrape run recorded: slot=%s status=%s fetched=%d inserted=%d updated=%d deleted=%d",
        schedule_slot,
        status,
        jobs_fetched,
        jobs_inserted,
        jobs_updated,
        jobs_deleted,
    )
    return cursor.lastrowid
