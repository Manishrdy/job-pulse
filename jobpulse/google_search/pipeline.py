"""Phase 2 search pipeline — wires the engine together (Module M1-7).

Per query: skip results we've cached recently → Google search → for each
result URL, dedup (cache + global_id + URL) → fetch & extract → keep only
in-region postings → ingest through the **Phase 1** ``ingest_jobs`` (which
re-checks global_id, applies the blocklist, scores relevance, syncs FTS).
Each run is logged to ``search_runs``.

Runs on its **own** lock — independent of the Phase 1 scrape lock — so a
manual Google search can overlap a scheduled scrape harmlessly (SQLite WAL
serializes the writes). A light status snapshot drives the dashboard, same
shape as :mod:`jobpulse.pipeline`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from typing import Any

import httpx

from jobpulse.config import AppConfig
from jobpulse.database import get_connection
from jobpulse.google_search.dedup import (
    cache_add,
    cache_has,
    global_id_exists,
    query_hash,
    url_exists,
)
from jobpulse.google_search.extractor import Fetch, extract
from jobpulse.google_search.rate_limiter import RateLimiter, RunAbortedError
from jobpulse.google_search.search_client import (
    CaptchaError,
    GoogleSearchClient,
    RateLimitedError,
    SearchError,
)
from jobpulse.google_search.url_parser import match_url
from jobpulse.ingest import ingest_jobs
from jobpulse.location import is_target_location
from jobpulse.models import JobRecord

log = logging.getLogger(__name__)

# Separate from the Phase 1 pipeline lock — Google search may overlap a scrape.
_search_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "last_run": None}
_state_lock = threading.Lock()

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def is_running() -> bool:
    with _state_lock:
        return _state.get("running", False)


def _set_state(**kw: Any) -> None:
    with _state_lock:
        _state.update(kw)


def record_search_run(
    conn: sqlite3.Connection,
    *,
    schedule_slot: str | None,
    queries_executed: int,
    urls_found: int,
    urls_new: int,
    jobs_inserted: int,
    jobs_skipped_dedup: int,
    jobs_skipped_blocked: int,
    duration_seconds: float | None,
    status: str,
    error_msg: str | None = None,
) -> int:
    """Insert a row into ``search_runs``. Returns the new row id."""
    cursor = conn.execute(
        """
        INSERT INTO search_runs (
            schedule_slot, queries_executed, urls_found, urls_new,
            jobs_inserted, jobs_skipped_dedup, jobs_skipped_blocked,
            duration_seconds, status, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schedule_slot, queries_executed, urls_found, urls_new,
            jobs_inserted, jobs_skipped_dedup, jobs_skipped_blocked,
            duration_seconds, status, error_msg,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _default_fetch(timeout: float = 15.0) -> Fetch:
    client = httpx.Client(timeout=timeout, follow_redirects=True, headers=_FETCH_HEADERS)
    return client.get


def run_google_search_pipeline(
    config: AppConfig,
    *,
    queries: Sequence[str],
    schedule_slot: str = "manual",
    search_client: GoogleSearchClient | None = None,
    fetch: Fetch | None = None,
    rate_limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    """Run the Google-search discovery channel over ``queries``.

    All collaborators are injectable for testing. Skips (without error) if a
    Google-search run is already in progress.
    """
    if not _search_lock.acquire(blocking=False):
        log.warning("Google search requested but one is already running — skipping")
        return {"status": "skipped", "reason": "another search in progress"}

    _set_state(running=True)
    conn = get_connection(config.database.path)
    client = search_client or GoogleSearchClient()
    fetch_fn: Fetch = fetch or _default_fetch()
    rl = rate_limiter or RateLimiter(max_queries=len(queries))
    started = time.monotonic()

    queries_executed = urls_found = urls_new = 0
    jobs_inserted = jobs_skipped_dedup = jobs_skipped_blocked = 0
    errors: list[str] = []
    status = "success"

    try:
        for i, query in enumerate(queries):
            try:
                rl.before_query(first=(i == 0))
            except RunAbortedError as exc:
                errors.append(str(exc))
                status = "partial"
                break

            qhash = query_hash(query)
            try:
                urls = client.search(query)
                rl.record_success()
            except (RateLimitedError, CaptchaError) as exc:
                log.warning("Google pushed back on %r: %s", query, exc)
                errors.append(str(exc))
                status = "rate_limited"
                try:
                    rl.record_failure()
                except RunAbortedError:
                    break
                continue
            except (SearchError, httpx.HTTPError) as exc:
                errors.append(f"{query}: {exc}")
                try:
                    rl.record_failure()
                except RunAbortedError:
                    status = "partial"
                    break
                continue

            queries_executed += 1
            urls_found += len(urls)

            records: list[JobRecord] = []
            for url in urls:
                match = match_url(url)
                if match is None:
                    continue  # unrecognized ATS URL
                norm = match.normalized_url
                if cache_has(conn, qhash, norm):
                    continue
                cache_add(conn, qhash, norm)
                if global_id_exists(conn, match.global_id) or url_exists(conn, norm):
                    jobs_skipped_dedup += 1
                    continue
                urls_new += 1
                rec = extract(match, fetch=fetch_fn)
                if rec is None:
                    continue
                remote = rec.is_remote == 1 if rec.is_remote is not None else None
                if not is_target_location(rec.location, rec.country_iso, remote, config.location):
                    continue
                records.append(rec)

            if records:
                stats = ingest_jobs(conn, records, target_roles=config.target_roles)
                jobs_inserted += stats.inserted
                jobs_skipped_blocked += stats.blocked

        if errors and status == "success":
            status = "partial"
        duration = round(time.monotonic() - started, 2)
        record_search_run(
            conn,
            schedule_slot=schedule_slot,
            queries_executed=queries_executed,
            urls_found=urls_found,
            urls_new=urls_new,
            jobs_inserted=jobs_inserted,
            jobs_skipped_dedup=jobs_skipped_dedup,
            jobs_skipped_blocked=jobs_skipped_blocked,
            duration_seconds=duration,
            status=status,
            error_msg="; ".join(errors[:5]) if errors else None,
        )
        outcome = {
            "status": status,
            "queries_executed": queries_executed,
            "urls_found": urls_found,
            "urls_new": urls_new,
            "jobs_inserted": jobs_inserted,
            "jobs_skipped_dedup": jobs_skipped_dedup,
            "jobs_skipped_blocked": jobs_skipped_blocked,
            "duration_seconds": duration,
            "errors": len(errors),
        }
        _set_state(last_run=outcome)
        log.info("Google search pipeline finished: %s", outcome)
        return outcome
    finally:
        conn.close()
        if search_client is None:
            client.close()
        _set_state(running=False)
        _search_lock.release()


def run_google_search_in_background(
    config: AppConfig, *, queries: Sequence[str], schedule_slot: str = "manual"
) -> bool:
    """Fire a Google search in a daemon thread. Returns False if already running."""
    if is_running():
        return False
    threading.Thread(
        target=lambda: run_google_search_pipeline(
            config, queries=list(queries), schedule_slot=schedule_slot
        ),
        daemon=True,
        name="jobpulse-google-search",
    ).start()
    return True
