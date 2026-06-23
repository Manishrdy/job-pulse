"""Per-company yield tracking — skip companies that never hire in-region.

A full scrape walks ~32k companies across every ATS, but most of them never
post a job in the target region (US/India). This module records, per company,
how often it was scraped, how often it was *reachable* (returned at least one
job), and how many of those jobs fell in the target region. From that history
it derives a skip set so later runs don't waste time re-fetching companies that
have proven to be foreign-only.

The signal is deliberately conservative (see [[jobpulse-open-work]]):

- "Productive" = posted **any** target-region job (region-only, role-agnostic).
  A US company that currently has only non-SWE openings still counts as
  productive, so we keep scraping it and catch its next SWE role immediately.
- A company is skipped only after ``skip_after_runs`` *reachable* runs in a row
  with zero target-region jobs. A run where the company returned **nothing**
  (``fetched == 0``) neither grows nor resets the streak — we can't tell a
  hiring lull or a dead slug from a genuinely foreign company, so we never skip
  on emptiness alone.
- Skipped companies are re-probed every ``recheck_days`` (the streak is left
  intact but ``last_scraped_at`` ages out of the cooldown window), so a foreign
  company that opens a US office is rediscovered.

The very first run after this ships skips nothing — there's no history yet.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

log = logging.getLogger(__name__)

_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"


@dataclass
class CompanyYield:
    """One company's outcome in a single scrape pass."""

    slug: str
    name: str
    fetched: int       # raw jobs returned (0 = unreachable / no openings)
    region_count: int  # of those, how many were in the target region


def load_skip_set(
    conn: sqlite3.Connection,
    *,
    skip_after_runs: int,
    recheck_days: int,
) -> set[tuple[str, str]]:
    """Return the ``(ats_type, slug)`` pairs to skip on this run.

    A company qualifies when its unproductive streak has reached the threshold
    **and** it was scraped recently enough to still be in its re-probe cooldown.
    Once ``last_scraped_at`` is older than ``recheck_days`` the pair drops out of
    this set and gets scraped again (re-probe).
    """
    rows = conn.execute(
        """
        SELECT ats_type, slug FROM company_yield
        WHERE unproductive_streak >= ?
          AND last_scraped_at IS NOT NULL
          AND last_scraped_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-' || ? || ' days')
        """,
        (skip_after_runs, recheck_days),
    ).fetchall()
    return {(row["ats_type"], row["slug"]) for row in rows}


def record_company_yield(conn: sqlite3.Connection, ats: str, y: CompanyYield) -> None:
    """Upsert one company's outcome from this run into ``company_yield``.

    Streak logic: reset to 0 on any target-region job; otherwise increment only
    when the company was reachable; leave untouched when it returned nothing.
    """
    reachable = 1 if y.fetched > 0 else 0
    if y.region_count > 0:
        initial_streak = 0
    elif reachable:
        initial_streak = 1
    else:
        initial_streak = 0

    conn.execute(
        f"""
        INSERT INTO company_yield (
            ats_type, slug, name, runs, reachable_runs, region_jobs_total,
            unproductive_streak, last_region_at, last_scraped_at
        ) VALUES (?, ?, ?, 1, ?, ?, ?, CASE WHEN ? > 0 THEN {_NOW_SQL} END, {_NOW_SQL})
        ON CONFLICT(ats_type, slug) DO UPDATE SET
            name = excluded.name,
            runs = runs + 1,
            reachable_runs = reachable_runs + ?,
            region_jobs_total = region_jobs_total + ?,
            unproductive_streak = CASE
                WHEN ? > 0 THEN 0
                WHEN ? > 0 THEN unproductive_streak + 1
                ELSE unproductive_streak END,
            last_region_at = CASE WHEN ? > 0 THEN {_NOW_SQL} ELSE last_region_at END,
            last_scraped_at = {_NOW_SQL}
        """,
        (
            # INSERT row
            ats, y.slug, y.name, reachable, y.region_count, initial_streak,
            y.region_count,
            # UPDATE branch
            reachable, y.region_count,
            y.region_count, reachable,
            y.region_count,
        ),
    )


def record_company_yields(conn: sqlite3.Connection, yields: list[tuple[str, CompanyYield]]) -> None:
    """Record many ``(ats, CompanyYield)`` outcomes and commit once."""
    for ats, y in yields:
        record_company_yield(conn, ats, y)
    conn.commit()
    log.info("Recorded yield for %d companies", len(yields))
