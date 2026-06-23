"""Read-only views over the ``company_yield`` table for the Companies page.

Surfaces which companies hire in the target region and which don't, from the
per-company history recorded by :mod:`jobpulse.company_yield`. Three views:

- ``foreign``    — reachable but never in-region long enough to be skipped
                   (``unproductive_streak >= skip_after_runs``).
- ``zero``       — returned jobs but zero in-region (superset of ``foreign``,
                   includes companies not yet at the skip threshold).
- ``productive`` — has posted at least one US/India job.

Companies that have never returned a job (``reachable_runs = 0``) are excluded
from ``foreign``/``zero`` on purpose: an empty fetch is a hiring lull or a dead
slug, not proof a company is foreign.
"""

from __future__ import annotations

import sqlite3

# Display cap so the page stays responsive on a 32k-company history.
ROW_LIMIT = 1000

_WHERE = {
    "foreign": "reachable_runs > 0 AND unproductive_streak >= :threshold",
    "zero": "reachable_runs > 0 AND region_jobs_total = 0",
    "productive": "region_jobs_total > 0",
    "all": "1 = 1",
}


def _table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_yield'"
        ).fetchone()
        is not None
    )


def counts(conn: sqlite3.Connection, *, skip_after_runs: int) -> dict[str, int]:
    """Row count for each view (for the filter tabs)."""
    if not _table_exists(conn):
        return {k: 0 for k in _WHERE}
    out: dict[str, int] = {}
    for view, where in _WHERE.items():
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM company_yield WHERE {where}",  # noqa: S608 — `where` is from the fixed _WHERE map
            {"threshold": skip_after_runs},
        ).fetchone()
        out[view] = row["n"]
    return out


def list_companies(
    conn: sqlite3.Connection, *, view: str, skip_after_runs: int
) -> list[dict]:
    """Rows for one view, newest-activity first, annotated with ``is_skipped``."""
    if not _table_exists(conn):
        return []
    where = _WHERE.get(view, _WHERE["foreign"])
    rows = conn.execute(
        f"""
        SELECT *, (reachable_runs > 0 AND unproductive_streak >= :threshold) AS is_skipped
        FROM company_yield
        WHERE {where}
        ORDER BY reachable_runs DESC, region_jobs_total DESC, name COLLATE NOCASE
        LIMIT {ROW_LIMIT}
        """,  # noqa: S608 — `where` is from the fixed _WHERE map; values are bound
        {"threshold": skip_after_runs},
    ).fetchall()
    return [dict(r) for r in rows]
