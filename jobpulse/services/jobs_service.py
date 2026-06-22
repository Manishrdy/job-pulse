"""Job feed service: listing with filters/sort/pagination, detail, view/expire.

All business logic for the main feed (FR-02, FR-03) lives here as plain
functions over a sqlite3 connection. Routes are a thin JSON layer on top.

The default feed shows active, non-blocked, non-expired jobs ordered by
``posted_at DESC NULLS LAST, first_seen DESC`` (FR-02.1). A free-text
``search`` switches the default ordering to FTS5 BM25 rank unless an
explicit ``sort`` is given.

``expire_job`` is the lifecycle action from Module 3, re-exported here so
the API has a single jobs surface (the implementation stays in cleanup.py).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from jobpulse.cleanup import expire_job  # re-exported (FR-06.3)

log = logging.getLogger(__name__)

__all__ = ["expire_job", "get_job", "list_jobs", "mark_viewed"]

_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

_VALID_SORTS = {"relevance", "posted", "salary"}

# Ordering fragments (j = jobs alias).
_ORDER_POSTED = "ORDER BY j.posted_at IS NULL, j.posted_at DESC, j.first_seen DESC"
_ORDER_RELEVANCE = "ORDER BY j.relevance_score DESC, j.first_seen DESC"
_ORDER_SALARY = (
    "ORDER BY j.salary_max IS NULL, j.salary_max DESC, j.salary_min DESC, j.first_seen DESC"
)
_ORDER_RANK = "ORDER BY rank"  # FTS5 bm25, best match first


def _fts_query(text: str) -> str:
    """Turn free-text into a safe FTS5 query: each token a quoted phrase (AND).

    Quoting neutralizes FTS operators/special chars; multiple tokens AND
    together (FTS5's implicit conjunction).
    """
    tokens = [t for t in text.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def list_jobs(
    conn: sqlite3.Connection,
    *,
    search: str | None = None,
    role: str | None = None,
    ats: str | None = None,
    location: str | None = None,
    remote_only: bool = False,
    employment_type: str | None = None,
    posted_within_days: int | None = None,
    salary_min: float | None = None,
    sort: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_blocked: bool = False,
    include_expired: bool = False,
    now: datetime | None = None,
) -> dict:
    """List jobs matching the given filters (all combinable; FR-03.5).

    Returns ``{"total": int, "limit": int, "offset": int, "jobs": [dict]}``
    where ``total`` is the unpaginated match count.
    """
    if sort is not None and sort not in _VALID_SORTS:
        raise ValueError(f"Invalid sort {sort!r}; expected one of {sorted(_VALID_SORTS)}")

    where: list[str] = []
    params: list[object] = []

    join = ""
    fts = _fts_query(search) if search else ""
    if fts:
        join = "JOIN jobs_fts ON jobs_fts.rowid = j.id"
        where.append("jobs_fts MATCH ?")
        params.append(fts)

    if not include_expired:
        where.append("j.status = 'active'")
    if not include_blocked:
        where.append("j.is_blocked = 0")
    if role:
        where.append("j.title LIKE ?")
        params.append(f"%{role}%")
    if ats:
        where.append("j.ats_type = ?")
        params.append(ats)
    if location:
        where.append("j.location LIKE ?")
        params.append(f"%{location}%")
    if remote_only:
        where.append("j.is_remote = 1")
    if employment_type:
        where.append("j.employment_type = ?")
        params.append(employment_type)
    if salary_min is not None:
        where.append("j.salary_min IS NOT NULL AND j.salary_min >= ?")
        params.append(salary_min)
    if posted_within_days is not None:
        reference = now if now is not None else datetime.now(UTC)
        cutoff = (reference.astimezone(UTC) - timedelta(days=posted_within_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        # Falls back to first_seen when posted_at is null (FR-03.3).
        where.append("datetime(COALESCE(j.posted_at, j.first_seen)) >= datetime(?)")
        params.append(cutoff)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Choose ordering (FR-03.6). With a search and no explicit sort, rank.
    if fts and sort is None:
        order_sql = _ORDER_RANK
    elif sort == "relevance":
        order_sql = _ORDER_RELEVANCE
    elif sort == "salary":
        order_sql = _ORDER_SALARY
    else:
        order_sql = _ORDER_POSTED

    count_sql = f"SELECT COUNT(*) AS c FROM jobs j {join} {where_sql}"
    total = conn.execute(count_sql, params).fetchone()["c"]

    list_sql = f"SELECT j.* FROM jobs j {join} {where_sql} {order_sql} LIMIT ? OFFSET ?"
    rows = conn.execute(list_sql, [*params, limit, offset]).fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": [dict(r) for r in rows],
    }


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    """Return a single job by id, or None (FR-02.4 detail view)."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def mark_viewed(conn: sqlite3.Connection, job_id: int) -> bool:
    """Stamp ``viewed_at`` on first view, clearing the "New" badge (FR-02.5).

    Only sets the timestamp when it's currently null, preserving the
    first-view time. Returns True if a row was updated.
    """
    cursor = conn.execute(
        f"UPDATE jobs SET viewed_at = {_NOW_SQL} WHERE id = ? AND viewed_at IS NULL",
        (job_id,),
    )
    conn.commit()
    return cursor.rowcount > 0
