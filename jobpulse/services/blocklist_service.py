"""Company blocklist service (FR-05).

Blocking a company both records it in ``company_blocklist`` and flips
``is_blocked = 1`` on every existing job from that company, so blocked
jobs disappear from the feed immediately (FR-05.2). Unblocking reverses
both. Jobs are never deleted by blocking — they're filtered at display
time (FR-05.4).
"""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)


def add_company(conn: sqlite3.Connection, company: str, reason: str | None = None) -> int:
    """Block a company (FR-05.1). Idempotent on the company name.

    Inserts into ``company_blocklist`` (or leaves an existing row intact)
    and marks all of that company's jobs blocked. Returns the blocklist id.
    Raises ValueError on an empty company name.
    """
    company = (company or "").strip()
    if not company:
        raise ValueError("company name is required")

    conn.execute(
        "INSERT OR IGNORE INTO company_blocklist (company, reason) VALUES (?, ?)",
        (company, reason),
    )
    conn.execute("UPDATE jobs SET is_blocked = 1 WHERE company = ?", (company,))
    conn.commit()

    row = conn.execute(
        "SELECT id FROM company_blocklist WHERE company = ?", (company,)
    ).fetchone()
    log.info("Blocked company %r (blocklist id %d)", company, row["id"])
    return row["id"]


def remove_company(conn: sqlite3.Connection, blocklist_id: int) -> bool:
    """Unblock by blocklist id (FR-05.3). Unhides that company's jobs.

    Returns True if a blocklist row was removed, False if id not found.
    """
    row = conn.execute(
        "SELECT company FROM company_blocklist WHERE id = ?", (blocklist_id,)
    ).fetchone()
    if row is None:
        return False

    company = row["company"]
    conn.execute("DELETE FROM company_blocklist WHERE id = ?", (blocklist_id,))
    conn.execute("UPDATE jobs SET is_blocked = 0 WHERE company = ?", (company,))
    conn.commit()
    log.info("Unblocked company %r (blocklist id %d)", company, blocklist_id)
    return True


def list_blocked(conn: sqlite3.Connection) -> list[dict]:
    """All blocked companies, most recently blocked first."""
    rows = conn.execute(
        "SELECT * FROM company_blocklist ORDER BY blocked_at DESC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]
