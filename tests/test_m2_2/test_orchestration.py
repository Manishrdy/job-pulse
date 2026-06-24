"""M2-2 — config knobs, cache cleanup, and cron query wiring."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from jobpulse import pipeline as p1_pipeline
from jobpulse.config import GoogleSearch
from jobpulse.google_search import pipeline as gs_pipeline
from jobpulse.google_search.query_builder import SLOT_PLAN, generate_queries

_TS = "%Y-%m-%dT%H:%M:%SZ"


# ── config model ───────────────────────────────────────────────────────────


def test_google_search_defaults():
    gs = GoogleSearch()
    assert gs.max_queries_per_run == 700
    assert gs.cache_ttl_hours == 24
    assert gs.min_delay <= gs.max_delay


def test_google_search_rejects_inverted_delays():
    with pytest.raises(ValueError):
        GoogleSearch(min_delay=10.0, max_delay=5.0)


def test_config_has_google_search_section(test_config):
    assert test_config.google_search.max_queries_per_run == 700


# ── cleanup prunes the search cache ────────────────────────────────────────


def test_cleanup_prunes_stale_cache(test_db: sqlite3.Connection, test_config):
    now = datetime.now(UTC)
    for qh, url, age_h in [
        ("h_old", "https://x/old", 30),
        ("h_new", "https://x/new", 1),
    ]:
        test_db.execute(
            "INSERT INTO search_results_cache (query_hash, url, discovered_at) "
            "VALUES (?, ?, ?)",
            (qh, url, (now - timedelta(hours=age_h)).strftime(_TS)),
        )
    test_db.commit()

    out = p1_pipeline.run_cleanup_pipeline(test_config)
    assert out["status"] == "success"
    assert out["cache_pruned"] == 1

    remaining = {
        r["query_hash"] for r in test_db.execute("SELECT query_hash FROM search_results_cache")
    }
    assert remaining == {"h_new"}


# ── pipeline honors the configured per-run cap ─────────────────────────────


class _FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        return []

    def close(self):
        pass


def test_pipeline_uses_config_query_cap(test_db: sqlite3.Connection, test_config):
    capped = test_config.model_copy(
        update={"google_search": GoogleSearch(max_queries_per_run=1)}
    )
    client = _FakeClient()
    out = gs_pipeline.run_google_search_pipeline(
        capped,
        queries=["q1", "q2"],
        search_client=client,
        fetch=lambda _u: None,
    )
    # Cap=1 → only the first query runs, overflow recorded as partial.
    assert client.calls == ["q1"]
    assert out["queries_executed"] == 1
    assert out["status"] == "partial"


# ── cron slot wiring ───────────────────────────────────────────────────────


def test_all_slots_generate_queries():
    from jobpulse.google_search.query_builder import load_locations

    locs = load_locations()
    for slot in SLOT_PLAN:
        assert len(generate_queries(locs, slot=slot)) > 0
