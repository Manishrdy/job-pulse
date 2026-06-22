#!/usr/bin/env python
"""Standalone scrape runner for OS cron (FR-01.3 / Module 8).

Runs one full scrape → ingest → log cycle and exits. Use this from an OS
crontab when you prefer external scheduling over the in-process scheduler
(``cron.enabled: false``). The in-app scheduler calls the same pipeline.

Usage:
    uv run python scripts/run_scrape.py [schedule_slot]

``schedule_slot`` is recorded in scrape_runs (default "manual").
"""

from __future__ import annotations

import sys

from jobpulse.config import load_config
from jobpulse.database import init_db
from jobpulse.logger import setup_logging
from jobpulse.pipeline import run_scrape_pipeline


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()  # pick up .env (JOBPULSE_CRON_ENABLED, JOBPULSE_CONFIG)
    config = load_config()
    setup_logging(config)
    init_db(config).close()  # ensure schema exists

    slot = sys.argv[1] if len(sys.argv) > 1 else "manual"
    result = run_scrape_pipeline(config, schedule_slot=slot)
    print(f"scrape ({slot}): {result}")
    return 0 if result.get("status") in ("success", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
