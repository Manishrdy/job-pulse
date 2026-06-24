#!/usr/bin/env python
"""Standalone Google-search runner for OS cron (Phase 2 / Module M2-2).

Generates the query set for a schedule slot from ``locations.yaml`` and runs
the Phase 2 discovery pipeline, then exits. Results land in the same ``jobs``
table as Phase 1 (source='google_search').

Usage:
    uv run python scripts/run_google_search.py [slot]

``slot`` is one of: morning | afternoon | evening | all (default "manual" →
treated as "all"). It's recorded on the search_runs row.

The per-run query cap (config.google_search.max_queries_per_run) applies: if
a slot generates more queries than the cap, the run stops at the cap and is
recorded as 'partial' (nothing is dropped silently — the cap message lands in
the run's error_msg). Raise the cap, add slots, or trim cities to cover more.
"""

from __future__ import annotations

import logging
import sys

from jobpulse.config import load_config
from jobpulse.database import init_db
from jobpulse.google_search.pipeline import run_google_search_pipeline
from jobpulse.google_search.query_builder import (
    SLOT_PLAN,
    generate_queries,
    load_locations,
)
from jobpulse.logger import setup_logging

log = logging.getLogger(__name__)

_SLOT_ARG_ALIASES = {"manual": None, "all": None}


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()  # pick up .env (JOBPULSE_CRON_ENABLED, JOBPULSE_CONFIG)
    config = load_config()
    setup_logging(config)
    init_db(config).close()  # ensure schema exists

    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg not in SLOT_PLAN and arg not in _SLOT_ARG_ALIASES:
        print(
            f"unknown slot {arg!r}; expected one of "
            f"{sorted(SLOT_PLAN) + sorted(_SLOT_ARG_ALIASES)}",
            file=sys.stderr,
        )
        return 2

    slot = None if arg in _SLOT_ARG_ALIASES else arg
    locations = load_locations()
    queries = generate_queries(locations, slot=slot)
    log.info("Generated %d queries for slot=%s", len(queries), arg)

    result = run_google_search_pipeline(
        config, queries=queries, schedule_slot=arg
    )
    print(f"google-search ({arg}): {result}")
    return 0 if result.get("status") in ("success", "partial", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
