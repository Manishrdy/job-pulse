#!/usr/bin/env python
"""Standalone TTL cleanup runner for OS cron (FR-06.1 / Module 8).

Deletes jobs older than the configured TTL and logs the run. Use from an
OS crontab for nightly cleanup when not using the in-process scheduler.

Usage:
    uv run python scripts/run_cleanup.py
"""

from __future__ import annotations

from jobpulse.config import load_config
from jobpulse.database import init_db
from jobpulse.logger import setup_logging
from jobpulse.pipeline import run_cleanup_pipeline


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()  # pick up .env (JOBPULSE_CRON_ENABLED, JOBPULSE_CONFIG)
    config = load_config()
    setup_logging(config)
    init_db(config).close()  # ensure schema exists

    result = run_cleanup_pipeline(config)
    print(f"cleanup: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
