"""Scraper orchestration: wrap jobhive scrapers, fetch in ATS priority order.

Drives the vendored jobhive per-ATS scrapers. jobhive exposes no
"search by role" API — each scraper fetches *all* jobs for one company
on one ATS. So the flow is:

1. Walk ATS platforms in configured priority order (primary → secondary
   → low-priority, per SCOPE §4.2 / FR-01.4).
2. For each ATS, read its company manifest CSV
   (``vendor/jobhive/ats-companies/{ats}.csv``).
3. Fetch every company, count the raw total, then keep only postings
   whose title matches a configured target role (FR-01.2).
4. Map survivors onto :class:`~jobpulse.models.JobRecord`.

The actual per-company fetch is injected (``scrape_fn``) so the
orchestration is unit-testable without network access.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from jobhive.exceptions import CompanyNotFoundError, ScraperError
from jobhive.models import Job as JobhiveJob
from jobhive.scrapers import get_scraper

from jobpulse.company_yield import CompanyYield
from jobpulse.config import AppConfig
from jobpulse.location import is_target_location
from jobpulse.models import JobRecord

log = logging.getLogger(__name__)

# Default location of the vendored company manifests.
DEFAULT_MANIFEST_DIR = Path(__file__).resolve().parent.parent / "vendor" / "jobhive" / "ats-companies"

# Workday's scraper expects the full careers URL, not a bare slug
# (see MODULE_0_AUDIT §6). Everything else takes the slug column.
_FULL_URL_ATS = {"workday"}

ScrapeFn = Callable[[str, str], list[JobhiveJob]]
# Called (in the main thread) as each company's jobs are fetched:
# (ats_type, raw_fetched_count, matched_records).
OnCompany = Callable[[str, int, list[JobRecord]], None]
# Predicate deciding whether a company should be skipped this run (it has
# proven, over prior runs, to never post in the target region). Injected so the
# scraper stays free of any database dependency.
SkipCompany = Callable[[str, "CompanyEntry"], bool]


@dataclass(frozen=True)
class CompanyEntry:
    name: str
    slug: str
    url: str


@dataclass
class AtsScrape:
    """Per-ATS slice of a scrape pass — drives per-ATS logging."""

    ats: str
    fetched: int = 0
    jobs: list[JobRecord] = field(default_factory=list)
    errors: int = 0
    duration: float = 0.0  # wall-clock seconds spent on this ATS
    skipped: int = 0  # companies skipped this run (proven unproductive)
    # Per-company outcomes this run, fed back to company_yield tracking.
    yields: list[tuple[str, CompanyYield]] = field(default_factory=list)


@dataclass
class ScrapeResult:
    """Outcome of a full scrape pass across all configured ATS platforms.

    Holds per-ATS slices (``ats_results``) plus a flat list of error
    messages. The ``ats_types`` / ``total_fetched`` / ``jobs`` properties
    preserve the original aggregate interface for existing callers.
    """

    ats_results: list[AtsScrape] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ats_types(self) -> list[str]:
        return [a.ats for a in self.ats_results]

    @property
    def total_fetched(self) -> int:
        return sum(a.fetched for a in self.ats_results)

    @property
    def jobs(self) -> list[JobRecord]:
        return [job for a in self.ats_results for job in a.jobs]


def ats_priority_order(config: AppConfig) -> list[str]:
    """ATS platforms in scrape priority: primary, then secondary, then low."""
    return config.ats_platforms.all_platforms


def load_company_manifest(ats: str, manifest_dir: Path | str | None = None) -> list[CompanyEntry]:
    """Read ``{manifest_dir}/{ats}.csv`` into CompanyEntry rows.

    Returns an empty list (with a warning) when the manifest is missing,
    so an unknown ATS doesn't abort the whole run.
    """
    base = Path(manifest_dir) if manifest_dir is not None else DEFAULT_MANIFEST_DIR
    path = base / f"{ats}.csv"
    if not path.exists():
        log.warning("No company manifest for ATS %r at %s", ats, path)
        return []

    entries: list[CompanyEntry] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            slug = (row.get("slug") or "").strip()
            if not slug:
                continue
            entries.append(
                CompanyEntry(
                    name=(row.get("name") or slug).strip(),
                    slug=slug,
                    url=(row.get("url") or "").strip(),
                )
            )
    return entries


def title_matches(title: str, roles: list[str]) -> bool:
    """True when the title contains any target role term (case-insensitive)."""
    if not title:
        return False
    haystack = title.lower()
    return any(role.strip().lower() in haystack for role in roles if role.strip())


def _scraper_arg(ats: str, entry: CompanyEntry) -> str:
    """The identifier jobhive's scraper expects for this ATS."""
    if ats in _FULL_URL_ATS and entry.url:
        return entry.url
    return entry.slug


def scrape_company(ats: str, identifier: str, *, timeout: float = 30.0) -> list[JobhiveJob]:
    """Fetch all jobs for one company on one ATS via jobhive.

    Errors are contained: a missing company or a scraper failure logs and
    returns an empty list rather than aborting the surrounding run.
    """
    try:
        scraper = get_scraper(ats, identifier, timeout=timeout)
        return scraper.fetch()
    except CompanyNotFoundError:
        log.warning("Company not found on %s: %s", ats, identifier)
        return []
    except ScraperError as exc:
        log.error("Scraper error on %s/%s: %s", ats, identifier, exc)
        return []


def distribute_workers(
    live_counts: dict[str, int],
    ceiling_for: Callable[[str], int],
    budget: int,
) -> dict[str, int]:
    """Split a global thread ``budget`` across ATS by live company count.

    Each ATS gets a share proportional to how many companies it will actually
    scrape this run (post cap + skip), clamped to ``[1, min(ceiling, count)]``
    so a rate-limit-sensitive ATS never exceeds its politeness ceiling and a
    tiny ATS never gets idle workers. If rounding overshoots the budget, trim
    from the largest allocation. Recomputed every run, so as company_yield
    prunes companies each ATS's share shrinks automatically.
    """
    active = {a: n for a, n in live_counts.items() if n > 0}
    if not active:
        return {}
    total = sum(active.values())
    alloc = {
        a: min(max(1, round(budget * n / total)), ceiling_for(a), n)
        for a, n in active.items()
    }
    while sum(alloc.values()) > budget and any(v > 1 for v in alloc.values()):
        biggest = max(alloc, key=lambda a: alloc[a])
        alloc[biggest] -= 1
    return alloc


def run_scrape(
    config: AppConfig,
    *,
    manifest_dir: Path | str | None = None,
    max_companies_per_ats: int | None = None,
    scrape_fn: ScrapeFn = scrape_company,
    concurrency: int | None = None,
    on_company: OnCompany | None = None,
    skip_company: SkipCompany | None = None,
) -> ScrapeResult:
    """Scrape every configured ATS, filter, and map.

    ATS platforms run in PARALLEL (each is a different host with its own rate
    limit), and companies *within* an ATS are fetched concurrently on a bounded
    inner pool. A global thread budget (``config.scrape.concurrency``) is split
    across ATS by live company count (:func:`distribute_workers`), capped per
    ATS by ``config.scrape.concurrency_for`` for politeness. ``ats_results``
    stays in priority order regardless of which ATS finishes first.

    When ``on_company`` is given, each company's matched records are handed to
    it as they're fetched (from that ATS's worker thread — the pipeline's
    callback opens a per-thread SQLite connection, so writes are thread-safe)
    and are *not* retained on the result — this lets the pipeline ingest
    incrementally so jobs appear live and survive a crash. Without it, records
    accumulate on ``result`` (the original behavior).

    ``scrape_fn(ats, identifier) -> list[Job]`` is injectable for tests.
    ``max_companies_per_ats`` caps companies per platform; ``concurrency``
    overrides the global budget (and per-ATS ceiling). When ``skip_company`` is given,
    companies it returns True for are dropped before fetching (proven to never
    post in the target region — see :mod:`jobpulse.company_yield`); the count is
    recorded on each ``AtsScrape.skipped``. Every fetched company's outcome is
    appended to ``AtsScrape.yields`` so the caller can update that tracking.
    """
    result = ScrapeResult()
    roles = config.target_roles

    def _scrape_one_ats(
        ats: str, companies: list[CompanyEntry], ats_slice: AtsScrape, workers: int
    ) -> None:
        """Fetch + filter one ATS's companies on a bounded inner pool.

        Runs in its own outer-pool thread, so it mutates only its own
        ``ats_slice``; it touches the shared ``result.errors`` (append-only,
        atomic) and the ``on_company`` callback (made thread-safe by the caller).
        """

        def _fetch_one(entry: CompanyEntry):
            identifier = _scraper_arg(ats, entry)
            try:
                return entry, scrape_fn(ats, identifier), None
            except Exception as exc:  # never let one company kill the run
                return entry, None, f"{ats}/{identifier}: {exc}"

        ats_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for entry, fetched, error in pool.map(_fetch_one, companies):
                if error is not None:
                    log.error("Scrape error: %s", error)
                    result.errors.append(error)
                    ats_slice.errors += 1
                    continue
                ats_slice.fetched += len(fetched)
                # One location check per job: region_count drives yield tracking
                # (region-only, role-agnostic), records keep only role matches.
                region_count = 0
                records: list[JobRecord] = []
                for job in fetched:
                    in_region = is_target_location(
                        job.location, job.country_iso, job.is_remote, config.location
                    )
                    if in_region:
                        region_count += 1
                        if title_matches(job.title, roles):
                            records.append(JobRecord.from_jobhive(job, company_name=entry.name))
                ats_slice.yields.append(
                    (ats, CompanyYield(entry.slug, entry.name, len(fetched), region_count))
                )
                if on_company is not None:
                    on_company(ats, len(fetched), records)
                else:
                    ats_slice.jobs.extend(records)
        ats_slice.duration = round(time.monotonic() - ats_started, 2)

    # Prep every ATS first (manifest → cap → skip filter), in priority order, so
    # we know each ATS's live company count before distributing the thread budget.
    preps: list[tuple[str, list[CompanyEntry], AtsScrape]] = []
    for ats in ats_priority_order(config):
        ats_slice = AtsScrape(ats=ats)
        result.ats_results.append(ats_slice)  # keeps priority order regardless of finish order
        companies = load_company_manifest(ats, manifest_dir)
        # Per-ATS cap (e.g. Workday=5) overrides the global cap; an explicit
        # max_companies_per_ats argument still wins for callers/tests.
        cap = max_companies_per_ats if max_companies_per_ats is not None else config.scrape.cap_for(ats)
        if cap is not None:
            companies = companies[:cap]
        # Drop companies proven (over prior runs) to never post in-region. The
        # re-probe cadence is baked into skip_company, so this is safe to apply
        # blindly here. Skipping happens after the cap so caps stay meaningful.
        if skip_company is not None:
            kept = [c for c in companies if not skip_company(ats, c)]
            ats_slice.skipped = len(companies) - len(kept)
            companies = kept
        preps.append((ats, companies, ats_slice))

    # Distribute the global thread budget across ATS by live company count. An
    # explicit ``concurrency`` arg forces that value as both budget and ceiling.
    live_counts = {ats: len(c) for ats, c, _ in preps if c}
    if concurrency is not None:
        budget = concurrency

        def ceiling_for(_ats: str) -> int:
            return concurrency
    else:
        budget = config.scrape.concurrency
        ceiling_for = config.scrape.concurrency_for
    alloc = distribute_workers(live_counts, ceiling_for, budget)

    # Run ATS in PARALLEL — each is a different host with its own rate limit.
    # Each ATS thread blocks on its own bounded inner pool, so total in-flight
    # company fetches stay within the budget.
    active = [(ats, c, s) for ats, c, s in preps if c]
    if active:
        with ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="ats") as outer:
            list(outer.map(lambda it: _scrape_one_ats(it[0], it[1], it[2], alloc[it[0]]), active))

    log.info(
        "Scrape pass complete: %d ATS, %d fetched, %d matched, %d skipped, %d errors (budget=%d, alloc=%s)",
        len(result.ats_results),
        result.total_fetched,
        len(result.jobs),
        sum(a.skipped for a in result.ats_results),
        len(result.errors),
        budget,
        alloc,
    )
    return result
