"""M1-3 — dedup (URL + global_id) and the search_results_cache TTL."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from jobpulse.google_search.dedup import (
    cache_add,
    cache_has,
    global_id_exists,
    prune_cache,
    url_exists,
)

_TS = "%Y-%m-%dT%H:%M:%SZ"


def _insert_cache_at(conn, query_hash, url, when: datetime):
    conn.execute(
        "INSERT INTO search_results_cache (query_hash, url, discovered_at) "
        "VALUES (?, ?, ?)",
        (query_hash, url, when.astimezone(UTC).strftime(_TS)),
    )
    conn.commit()


# ── global_id / url existence ─────────────────────────────────────────────


def test_global_id_exists(test_db: sqlite3.Connection, seed):
    seed(test_db, global_id="greenhouse:12345")
    assert global_id_exists(test_db, "greenhouse:12345")
    assert not global_id_exists(test_db, "greenhouse:99999")


def test_global_id_catches_phase1_overlap(test_db: sqlite3.Connection, seed):
    """A jobhive-sourced row blocks a Phase 2 re-insert of the same posting."""
    seed(test_db, global_id="lever:abc-123", source="jobhive")
    assert global_id_exists(test_db, "lever:abc-123")


def test_url_exists(test_db: sqlite3.Connection, seed):
    seed(test_db, url="https://boards.greenhouse.io/acme/jobs/1")
    assert url_exists(test_db, "https://boards.greenhouse.io/acme/jobs/1")
    assert not url_exists(test_db, "https://boards.greenhouse.io/acme/jobs/2")


# ── cache ─────────────────────────────────────────────────────────────────


def test_cache_add_then_has(test_db: sqlite3.Connection):
    assert not cache_has(test_db, "h1", "https://x/1")
    cache_add(test_db, "h1", "https://x/1")
    assert cache_has(test_db, "h1", "https://x/1")


def test_cache_add_is_idempotent(test_db: sqlite3.Connection):
    cache_add(test_db, "h1", "https://x/1")
    cache_add(test_db, "h1", "https://x/1")  # no IntegrityError
    n = test_db.execute("SELECT COUNT(*) AS c FROM search_results_cache").fetchone()["c"]
    assert n == 1


def test_cache_miss_for_different_query(test_db: sqlite3.Connection):
    cache_add(test_db, "h1", "https://x/1")
    assert not cache_has(test_db, "h2", "https://x/1")


def test_cache_has_respects_ttl(test_db: sqlite3.Connection):
    old = datetime.now(UTC) - timedelta(hours=25)
    _insert_cache_at(test_db, "h1", "https://x/1", old)
    # Older than the 24h window → treated as a miss.
    assert not cache_has(test_db, "h1", "https://x/1", ttl_hours=24)
    # Widen the window → hit.
    assert cache_has(test_db, "h1", "https://x/1", ttl_hours=48)


def test_prune_cache_removes_stale_only(test_db: sqlite3.Connection):
    now = datetime.now(UTC)
    _insert_cache_at(test_db, "h_old", "https://x/old", now - timedelta(hours=30))
    _insert_cache_at(test_db, "h_new", "https://x/new", now - timedelta(hours=1))
    removed = prune_cache(test_db, ttl_hours=24, now=now)
    assert removed == 1
    remaining = {
        r["query_hash"] for r in test_db.execute("SELECT query_hash FROM search_results_cache")
    }
    assert remaining == {"h_new"}
