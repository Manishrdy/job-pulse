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
from jobpulse.ingest import ingest_jobs, record_scrape_run, record_scrape_run_ats
from jobpulse.scraper import ScrapeFn, run_scrape

log = logging.getLogger(__name__)

# One lock for the whole pipeline: scrape and cleanup never run concurrently.
_pipeline_lock = threading.Lock()

def _empty_progress() -> dict[str, Any]:
    return {"current_ats": None, "fetched": 0, "inserted": 0, "updated": 0, "blocked": 0, "per_ats": []}


# Lightweight shared state for the UI to display.
_state: dict[str, Any] = {
    "running": False,
    "current": None,        # "scrape" | "cleanup" | None
    "progress": _empty_progress(),  # live counters during a scrape
    "last_scrape": None,    # result dict
    "last_cleanup": None,   # result dict
}
_state_lock = threading.Lock()


def get_status() -> dict[str, Any]:
    """Snapshot of pipeline state for the dashboard."""
    with _state_lock:
        # Deep-ish copy so callers don't see the dict mutate mid-render.
        snap = dict(_state)
        snap["progress"] = dict(_state["progress"])
        snap["progress"]["per_ats"] = list(_state["progress"]["per_ats"])
        return snap


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def _set_progress(progress: dict[str, Any]) -> None:
    with _state_lock:
        _state["progress"] = progress


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

    progress = _empty_progress()
    _set_state(running=True, current="scrape")
    _set_progress(progress)
    conn = get_connection(config.database.path)
    started = time.monotonic()

    # Per-ATS accumulators, keyed by ats_type. Jobs are ingested and committed
    # incrementally as each company is fetched, so they appear in the feed live
    # and a mid-run crash keeps everything scraped so far.
    per_ats: dict[str, dict] = {}

    def _agg(ats: str) -> dict:
        return per_ats.setdefault(
            ats,
            {"ats_type": ats, "fetched": 0, "inserted": 0, "updated": 0, "blocked": 0, "errors": 0},
        )

    def _on_company(ats: str, fetched_count: int, records: list) -> None:
        stats = ingest_jobs(conn, records, target_roles=config.target_roles)  # commits
        a = _agg(ats)
        a["inserted"] += stats.inserted
        a["updated"] += stats.updated
        a["blocked"] += stats.blocked
        # Update live progress snapshot for the dashboard poller.
        progress["current_ats"] = ats
        progress["fetched"] += fetched_count
        progress["inserted"] += stats.inserted
        progress["updated"] += stats.updated
        progress["blocked"] += stats.blocked
        progress["per_ats"] = [dict(v) for v in per_ats.values()]
        _set_progress(dict(progress))

    try:
        kwargs: dict[str, Any] = {
            "max_companies_per_ats": config.scrape.max_companies_per_ats,
            "on_company": _on_company,
        }
        if scrape_fn is not None:
            kwargs["scrape_fn"] = scrape_fn
        if manifest_dir is not None:
            kwargs["manifest_dir"] = manifest_dir

        result = run_scrape(config, **kwargs)

        # Fill in fetched/errors counts now that each ATS is fully scraped.
        for slice_ in result.ats_results:
            a = _agg(slice_.ats)
            a["fetched"] = slice_.fetched
            a["errors"] = slice_.errors

        totals = {
            "inserted": sum(a["inserted"] for a in per_ats.values()),
            "updated": sum(a["updated"] for a in per_ats.values()),
            "blocked": sum(a["blocked"] for a in per_ats.values()),
        }
        duration = round(time.monotonic() - started, 2)
        status = "partial_failure" if result.errors else "success"
        error_msg = "; ".join(result.errors[:5]) if result.errors else None

        run_id = record_scrape_run(
            conn,
            schedule_slot=schedule_slot,
            ats_types_scraped=result.ats_types,
            jobs_fetched=result.total_fetched,
            jobs_inserted=totals["inserted"],
            jobs_updated=totals["updated"],
            jobs_blocked=totals["blocked"],
            duration_seconds=duration,
            status=status,
            error_msg=error_msg,
        )
        record_scrape_run_ats(conn, run_id, [_agg(a) for a in result.ats_types])

        outcome = {
            "status": status,
            "fetched": result.total_fetched,
            "inserted": totals["inserted"],
            "updated": totals["updated"],
            "blocked": totals["blocked"],
            "errors": len(result.errors),
            "duration_seconds": duration,
            "per_ats": list(per_ats.values()),
        }
        _set_state(last_scrape=outcome)
        log.info("Scrape pipeline finished: %s", outcome)
        return outcome
    except Exception as exc:  # record the failure (partial work already committed)
        duration = round(time.monotonic() - started, 2)
        partial = {
            "inserted": sum(a["inserted"] for a in per_ats.values()),
            "updated": sum(a["updated"] for a in per_ats.values()),
            "blocked": sum(a["blocked"] for a in per_ats.values()),
        }
        run_id = record_scrape_run(
            conn,
            schedule_slot=schedule_slot,
            ats_types_scraped=list(per_ats.keys()),
            jobs_fetched=progress["fetched"],
            jobs_inserted=partial["inserted"],
            jobs_updated=partial["updated"],
            jobs_blocked=partial["blocked"],
            duration_seconds=duration,
            status="failure",
            error_msg=str(exc),
        )
        if per_ats:
            record_scrape_run_ats(conn, run_id, list(per_ats.values()))
        _set_state(last_scrape={"status": "failure", "error": str(exc), **partial})
        log.exception("Scrape pipeline failed (partial work kept)")
        raise
    finally:
        conn.close()
        _set_state(running=False, current=None)
        _set_progress(_empty_progress())
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
