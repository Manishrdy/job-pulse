"""Analytics service — the aggregate queries behind the dashboard (FR-07 / §6).

Each function returns plain Python lists/dicts ready for JSON. ``summary``
bundles everything the analytics page needs in one call (the
``/api/analytics/summary`` endpoint).

Role-wise breakdown (FR-07.1) is computed in Python rather than from a
stored ``matched_role`` column: a single posting can match several target
roles ("Senior Software Engineer" matches both "Software Engineer" and
"Senior Software Engineer"), so we count each role independently by
case-insensitive title match. Counts can therefore overlap — that's
intended for a per-keyword view.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

# A "response" is any movement past the initial application that isn't a
# silent drop — used for the response-rate summary card.
_RESPONDED_STATUSES = ("phone_screen", "interview", "offer", "rejected")


def role_breakdown(conn: sqlite3.Connection, target_roles: list[str]) -> list[dict]:
    """Per-role seen vs applied counts (FR-07.1)."""
    out: list[dict] = []
    for role in target_roles:
        term = role.strip().lower()
        if not term:
            continue
        like = f"%{term}%"
        seen = conn.execute(
            "SELECT COUNT(*) AS c FROM jobs "
            "WHERE status = 'active' AND is_blocked = 0 AND LOWER(title) LIKE ?",
            (like,),
        ).fetchone()["c"]
        applied = conn.execute(
            "SELECT COUNT(*) AS c FROM applied_jobs WHERE LOWER(title) LIKE ?",
            (like,),
        ).fetchone()["c"]
        out.append({"role": role, "seen": seen, "applied": applied})
    return out


def _cutoff(days: int, now: datetime | None) -> str:
    reference = now if now is not None else datetime.now(UTC)
    return (reference.astimezone(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def applications_per_day(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Applications grouped by calendar day, newest first (FR-07.2).

    When ``days`` is given, only applications within the last ``days`` are
    included (date-range filter).
    """
    if days is not None:
        rows = conn.execute(
            "SELECT DATE(applied_at) AS day, COUNT(*) AS count FROM applied_jobs "
            "WHERE datetime(applied_at) >= datetime(?) GROUP BY day ORDER BY day DESC",
            (_cutoff(days, now),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DATE(applied_at) AS day, COUNT(*) AS count "
            "FROM applied_jobs GROUP BY day ORDER BY day DESC"
        ).fetchall()
    return [{"day": r["day"], "count": r["count"]} for r in rows]


def ats_breakdown(conn: sqlite3.Connection) -> dict:
    """Active jobs per ATS and applications per ATS (FR-07.3)."""
    jobs_rows = conn.execute(
        "SELECT ats_type, COUNT(*) AS count FROM jobs "
        "WHERE status = 'active' AND is_blocked = 0 "
        "GROUP BY ats_type ORDER BY count DESC"
    ).fetchall()
    applied_rows = conn.execute(
        "SELECT ats_type, COUNT(*) AS count FROM applied_jobs "
        "GROUP BY ats_type ORDER BY count DESC"
    ).fetchall()
    return {
        "jobs": [{"ats_type": r["ats_type"], "count": r["count"]} for r in jobs_rows],
        "applied": [{"ats_type": r["ats_type"], "count": r["count"]} for r in applied_rows],
    }


def source_breakdown(conn: sqlite3.Connection) -> list[dict]:
    """Active jobs per discovery channel (jobhive vs google_search)."""
    rows = conn.execute(
        "SELECT source, COUNT(*) AS count FROM jobs "
        "WHERE status = 'active' AND is_blocked = 0 "
        "GROUP BY source ORDER BY count DESC"
    ).fetchall()
    return [{"source": r["source"], "count": r["count"]} for r in rows]


def status_funnel(conn: sqlite3.Connection) -> list[dict]:
    """Application counts by status (FR-07.4)."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM applied_jobs GROUP BY status"
    ).fetchall()
    return [{"status": r["status"], "count": r["count"]} for r in rows]


def scrape_trends(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Jobs inserted per day from scrape runs, oldest first (FR-07.5).

    When ``days`` is given, only runs within the last ``days`` are included.
    """
    if days is not None:
        rows = conn.execute(
            "SELECT DATE(run_at) AS day, SUM(jobs_inserted) AS inserted FROM scrape_runs "
            "WHERE datetime(run_at) >= datetime(?) GROUP BY day ORDER BY day ASC",
            (_cutoff(days, now),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DATE(run_at) AS day, SUM(jobs_inserted) AS inserted "
            "FROM scrape_runs GROUP BY day ORDER BY day ASC"
        ).fetchall()
    return [{"day": r["day"], "inserted": r["inserted"] or 0} for r in rows]


def summary(
    conn: sqlite3.Connection,
    target_roles: list[str],
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> dict:
    """All analytics data plus summary cards in one payload.

    Cards: total active jobs, total applied, applications in the last 7
    days, and response rate (responded / total applied). When ``days`` is
    given, the time-series sections (applications-per-day, scrape-trends)
    are scoped to that window; the cards remain whole-history.
    """
    total_active = conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE status = 'active' AND is_blocked = 0"
    ).fetchone()["c"]
    total_applied = conn.execute("SELECT COUNT(*) AS c FROM applied_jobs").fetchone()["c"]

    reference = now if now is not None else datetime.now(UTC)
    week_ago = (reference.astimezone(UTC) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    applications_this_week = conn.execute(
        "SELECT COUNT(*) AS c FROM applied_jobs WHERE datetime(applied_at) >= datetime(?)",
        (week_ago,),
    ).fetchone()["c"]

    placeholders = ",".join("?" for _ in _RESPONDED_STATUSES)
    responded = conn.execute(
        f"SELECT COUNT(*) AS c FROM applied_jobs WHERE status IN ({placeholders})",
        _RESPONDED_STATUSES,
    ).fetchone()["c"]
    response_rate = round(responded / total_applied, 4) if total_applied else 0.0

    # "Google-only finds": active jobs the Google channel surfaced. Because
    # both channels dedup on global_id, a google_search row is one jobhive
    # never inserted — i.e. unique value the Google channel added.
    google_only_finds = conn.execute(
        "SELECT COUNT(*) AS c FROM jobs "
        "WHERE status = 'active' AND is_blocked = 0 AND source = 'google_search'"
    ).fetchone()["c"]

    return {
        "cards": {
            "total_active_jobs": total_active,
            "total_applied": total_applied,
            "applications_this_week": applications_this_week,
            "response_rate": response_rate,
            "google_only_finds": google_only_finds,
        },
        "days": days,
        "role_breakdown": role_breakdown(conn, target_roles),
        "applications_per_day": applications_per_day(conn, days=days, now=now),
        "ats_breakdown": ats_breakdown(conn),
        "source_breakdown": source_breakdown(conn),
        "status_funnel": status_funnel(conn),
        "scrape_trends": scrape_trends(conn, days=days, now=now),
    }
