#!/usr/bin/env python
"""One-off / repeatable purge of non-target-country jobs from the DB.

Classifies every stored job by its location field and deletes the ones that
are a *confirmed* different country (never the ambiguous UNKNOWN ones). This
is also run automatically at the start of each scrape; use this script to
clean the existing DB immediately without waiting for a scrape.

Usage:
    uv run python scripts/purge_locations.py            # delete confirmed-foreign
    uv run python scripts/purge_locations.py --dry-run  # report only, delete nothing
"""

from __future__ import annotations

import sys
from collections import Counter

from jobpulse.config import load_config
from jobpulse.database import get_connection
from jobpulse.location import LocationMatch, classify_location, purge_non_target_location


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    config = load_config()
    conn = get_connection(config.database.path)

    rows = conn.execute("SELECT id, location, country_iso FROM jobs").fetchall()
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = {"NON_US": []}
    for r in rows:
        match = classify_location(r["location"], r["country_iso"], config.location.country_code)
        counts[match.value] += 1
        if match is LocationMatch.NON_US and len(samples["NON_US"]) < 12:
            samples["NON_US"].append(r["location"] or "(blank)")

    total = len(rows)
    print(f"Target country : {config.location.country_code}")
    print(f"Total jobs     : {total}")
    print(f"  matches (US) : {counts.get('US', 0)}")
    print(f"  foreign      : {counts.get('NON_US', 0)}  <- will be deleted")
    print(f"  unknown      : {counts.get('UNKNOWN', 0)}  (kept)")
    print("Sample foreign locations:")
    for loc in samples["NON_US"]:
        print(f"  - {loc}")

    if dry_run:
        print("\n--dry-run: nothing deleted.")
        conn.close()
        return 0

    deleted = purge_non_target_location(conn, config.location)
    remaining = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    print(f"\nDeleted {deleted} foreign jobs. Remaining: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
