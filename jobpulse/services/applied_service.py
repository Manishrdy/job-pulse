"""Applied-jobs tracker service (FR-04).

"Mark applied" moves a row out of the live ``jobs`` feed and into the
permanent ``applied_jobs`` table (FR-04.1). From there the user tracks it
through a status pipeline and annotates it with notes / a follow-up date
(FR-04.3). The applied table is never reaped by the TTL cleanup.
"""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)

_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

# Allowed values for applied_jobs.status (FR-04.3).
VALID_STATUSES = {
    "applied",
    "phone_screen",
    "interview",
    "offer",
    "rejected",
    "ghosted",
}


def mark_applied(conn: sqlite3.Connection, job_id: int) -> int | None:
    """Move a job from ``jobs`` to ``applied_jobs`` (FR-04.1).

    Returns the new ``applied_jobs.id``. Returns None if the job doesn't
    exist. If the job was already applied (same ``global_id`` present in
    ``applied_jobs``), the live row is removed and the existing applied id
    is returned (idempotent).
    """
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        log.warning("mark_applied: job %d not found", job_id)
        return None

    existing = conn.execute(
        "SELECT id FROM applied_jobs WHERE global_id = ?", (job["global_id"],)
    ).fetchone()
    if existing is not None:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return existing["id"]

    cursor = conn.execute(
        """
        INSERT INTO applied_jobs (
            global_id, url, apply_url, title, company, ats_type, location,
            is_remote, salary_summary, employment_type, description,
            posted_at, first_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job["global_id"],
            job["url"],
            job["apply_url"],
            job["title"],
            job["company"],
            job["ats_type"],
            job["location"],
            job["is_remote"],
            job["salary_summary"],
            job["employment_type"],
            job["description"],
            job["posted_at"],
            job["first_seen"],
        ),
    )
    applied_id = cursor.lastrowid
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    log.info("Job %d (%s) marked applied -> applied_jobs %d", job_id, job["global_id"], applied_id)
    return applied_id


def list_applied(
    conn: sqlite3.Connection,
    *,
    search: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List applied jobs with optional text search and status filter (FR-04.2).

    Returns ``{"total", "limit", "offset", "jobs"}``. Ordered by most
    recently applied first.
    """
    where: list[str] = []
    params: list[object] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if search:
        like = f"%{search}%"
        where.append("(title LIKE ? OR company LIKE ?)")
        params.extend([like, like])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM applied_jobs {where_sql}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"SELECT * FROM applied_jobs {where_sql} ORDER BY applied_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": [dict(r) for r in rows],
    }


def update_applied(
    conn: sqlite3.Connection,
    applied_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
    follow_up_date: str | None = None,
) -> bool:
    """Update an applied job's status / notes / follow-up date (FR-04.3).

    Only the provided fields are changed; ``updated_at`` always bumps.
    Raises ValueError on an invalid status. Returns True if a row changed.
    """
    sets: list[str] = []
    params: list[object] = []

    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}"
            )
        sets.append("status = ?")
        params.append(status)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if follow_up_date is not None:
        sets.append("follow_up_date = ?")
        params.append(follow_up_date)

    if not sets:
        return False

    sets.append(f"updated_at = {_NOW_SQL}")
    cursor = conn.execute(
        f"UPDATE applied_jobs SET {', '.join(sets)} WHERE id = ?",
        [*params, applied_id],
    )
    conn.commit()
    return cursor.rowcount > 0


def get_applied(conn: sqlite3.Connection, applied_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM applied_jobs WHERE id = ?", (applied_id,)).fetchone()
    return dict(row) if row else None
