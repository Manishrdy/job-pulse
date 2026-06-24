"""Idempotency for the Phase 2 pipeline (Module M1-3).

Three guards keep us from re-doing work or storing duplicates:

- :func:`global_id_exists` — the authoritative check. A matched URL yields a
  ``{ats}:{id}`` global_id (see :mod:`~jobpulse.google_search.url_parser`);
  if that already sits in ``jobs`` we skip — this catches both prior Phase 2
  inserts *and* jobs Phase 1 (jobhive) already stored under its own URL.
- :func:`url_exists` — a fallback for URLs we can't pattern-match (no
  global_id). Phase 2 stores the *normalized* URL on insert, so a repeat
  result matches exactly.
- ``search_results_cache`` — :func:`cache_has` / :func:`cache_add` /
  :func:`prune_cache` avoid re-fetching the same ``(query, url)`` within a
  TTL window (default 24h). Pruned by the nightly cleanup.

``ingest_jobs`` still enforces the UNIQUE ``global_id`` constraint as the
last line of defense; these checks just let us skip the fetch/extract work
before it gets that far.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

# Matches the DB column DEFAULT format; fixed-width so lexicographic string
# comparison equals chronological order (same convention as cleanup.py).
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def query_hash(query: str) -> str:
    """SHA256 of the (whitespace-normalized) query — the cache key."""
    normalized = " ".join(query.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def global_id_exists(conn: sqlite3.Connection, global_id: str) -> bool:
    """True if a job with this global_id is already stored."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE global_id = ? LIMIT 1", (global_id,)
    ).fetchone()
    return row is not None


def url_exists(conn: sqlite3.Connection, normalized_url: str) -> bool:
    """True if a job with this exact (normalized) URL is already stored."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (normalized_url,)
    ).fetchone()
    return row is not None


def _cutoff(ttl_hours: int, now: datetime | None) -> str:
    reference = now if now is not None else datetime.now(UTC)
    return (reference.astimezone(UTC) - timedelta(hours=ttl_hours)).strftime(_TS_FORMAT)


def cache_has(
    conn: sqlite3.Connection,
    query_hash: str,
    url: str,
    *,
    ttl_hours: int = 24,
    now: datetime | None = None,
) -> bool:
    """True if ``(query_hash, url)`` was cached within the TTL window."""
    row = conn.execute(
        "SELECT 1 FROM search_results_cache "
        "WHERE query_hash = ? AND url = ? AND discovered_at >= ? LIMIT 1",
        (query_hash, url, _cutoff(ttl_hours, now)),
    ).fetchone()
    return row is not None


def cache_add(conn: sqlite3.Connection, query_hash: str, url: str) -> None:
    """Record a ``(query_hash, url)`` pair. No-op if already present."""
    conn.execute(
        "INSERT OR IGNORE INTO search_results_cache (query_hash, url) VALUES (?, ?)",
        (query_hash, url),
    )
    conn.commit()


def prune_cache(
    conn: sqlite3.Connection,
    *,
    ttl_hours: int = 24,
    now: datetime | None = None,
) -> int:
    """Delete cache rows older than the TTL. Returns the count removed."""
    cursor = conn.execute(
        "DELETE FROM search_results_cache WHERE discovered_at < ?",
        (_cutoff(ttl_hours, now),),
    )
    conn.commit()
    return cursor.rowcount
