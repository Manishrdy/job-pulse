"""End-to-end pipeline orchestration (Module 8).

Ties together the pieces built in earlier modules into two top-level
operations, each recorded to ``scrape_runs``:

- :func:`run_scrape_pipeline` — scrape every configured ATS, ingest the
  matches, and log the run (FR-01.7).
- :func:`run_cleanup_pipeline` — TTL-delete stale jobs and log it.

A single process-wide lock serializes all pipeline runs so a scheduled
run and a UI-triggered run (or two clicks) can never overlap and corrupt
counts. Runs are also idempotent — ingestion dedups on ``global_id`` — so
an interrupted run (e.g. app restart mid-scrape) is safe to repeat.

The same functions back the cron scheduler, the standalone cron scripts,
and the dev-only UI trigger.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from jobpulse.cleanup import cleanup_old_jobs
from jobpulse.config import AppConfig
from jobpulse.database import get_connection
from jobpulse.ingest import ingest_jobs, record_scrape_run
from jobpulse.scraper import ScrapeFn, run_scrape

log = logging.getLogger(__name__)

# One lock for the whole pipeline: scrape and cleanup never run concurrently.
_pipeline_lock = threading.Lock()

# Lightweight shared state for the UI to display.
_state: dict[str, Any] = {
    "running": False,
    "current": None,        # "scrape" | "cleanup" | None
    "last_scrape": None,    # result dict
    "last_cleanup": None,   # result dict
}
_state_lock = threading.Lock()


def get_status() -> dict[str, Any]:
    """Snapshot of pipeline state for the dashboard."""
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def is_running() -> bool:
    return _state.get("running", False)


def run_scrape_pipeline(
    config: AppConfig,
    *,
    schedule_slot: str = "manual",
    scrape_fn: ScrapeFn | None = None,
    manifest_dir: str | None = None,
) -> dict[str, Any]:
    """Scrape → ingest → record. Returns a result dict.

    Skips (without error) if another pipeline run is already in progress.
    ``scrape_fn`` / ``manifest_dir`` are injectable for testing.
    """
    if not _pipeline_lock.acquire(blocking=False):
        log.warning("Scrape requested but a pipeline run is already in progress — skipping")
        return {"status": "skipped", "reason": "another run in progress"}

    _set_state(running=True, current="scrape")
    conn = get_connection(config.database.path)
    started = time.monotonic()
    try:
        kwargs: dict[str, Any] = {
            "max_companies_per_ats": config.scrape.max_companies_per_ats,
        }
        if scrape_fn is not None:
            kwargs["scrape_fn"] = scrape_fn
        if manifest_dir is not None:
            kwargs["manifest_dir"] = manifest_dir

        result = run_scrape(config, **kwargs)
        stats = ingest_jobs(conn, result.jobs, target_roles=config.target_roles)
        duration = round(time.monotonic() - started, 2)
        status = "partial_failure" if result.errors else "success"
        error_msg = "; ".join(result.errors[:5]) if result.errors else None

        record_scrape_run(
            conn,
            schedule_slot=schedule_slot,
            ats_types_scraped=result.ats_types,
            jobs_fetched=result.total_fetched,
            jobs_inserted=stats.inserted,
            jobs_updated=stats.updated,
            duration_seconds=duration,
            status=status,
            error_msg=error_msg,
        )
        outcome = {
            "status": status,
            "fetched": result.total_fetched,
            "inserted": stats.inserted,
            "updated": stats.updated,
            "blocked": stats.blocked,
            "errors": len(result.errors),
            "duration_seconds": duration,
        }
        _set_state(last_scrape=outcome)
        log.info("Scrape pipeline finished: %s", outcome)
        return outcome
    except Exception as exc:  # record the failure, then re-raise
        duration = round(time.monotonic() - started, 2)
        record_scrape_run(
            conn,
            schedule_slot=schedule_slot,
            ats_types_scraped="",
            jobs_fetched=0,
            jobs_inserted=0,
            jobs_updated=0,
            duration_seconds=duration,
            status="failure",
            error_msg=str(exc),
        )
        _set_state(last_scrape={"status": "failure", "error": str(exc)})
        log.exception("Scrape pipeline failed")
        raise
    finally:
        conn.close()
        _set_state(running=False, current=None)
        _pipeline_lock.release()


def run_cleanup_pipeline(config: AppConfig) -> dict[str, Any]:
    """TTL cleanup → record. Returns a result dict; skips if a run is active."""
    if not _pipeline_lock.acquire(blocking=False):
        log.warning("Cleanup requested but a pipeline run is already in progress — skipping")
        return {"status": "skipped", "reason": "another run in progress"}

    _set_state(running=True, current="cleanup")
    conn = get_connection(config.database.path)
    started = time.monotonic()
    try:
        deleted = cleanup_old_jobs(conn, config.data_lifecycle.ttl_days)
        duration = round(time.monotonic() - started, 2)
        record_scrape_run(
            conn,
            schedule_slot="cleanup",
            ats_types_scraped="",
            jobs_fetched=0,
            jobs_inserted=0,
            jobs_updated=0,
            jobs_deleted=deleted,
            duration_seconds=duration,
            status="success",
        )
        outcome = {"status": "success", "deleted": deleted, "duration_seconds": duration}
        _set_state(last_cleanup=outcome)
        log.info("Cleanup pipeline finished: %s", outcome)
        return outcome
    finally:
        conn.close()
        _set_state(running=False, current=None)
        _pipeline_lock.release()


def run_scrape_in_background(config: AppConfig, *, schedule_slot: str = "manual") -> bool:
    """Fire a scrape in a daemon thread. Returns False if already running."""
    if is_running():
        return False
    threading.Thread(
        target=run_scrape_pipeline,
        args=(config,),
        kwargs={"schedule_slot": schedule_slot},
        daemon=True,
        name="jobpulse-scrape",
    ).start()
    return True


def run_cleanup_in_background(config: AppConfig) -> bool:
    """Fire a cleanup in a daemon thread. Returns False if already running."""
    if is_running():
        return False
    threading.Thread(
        target=run_cleanup_pipeline,
        args=(config,),
        daemon=True,
        name="jobpulse-cleanup",
    ).start()
    return True
