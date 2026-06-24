"""Config-driven Google query generation for Phase 2 (Module M2-1, reworked).

Builds `site:` search strings from the user's **config** — every
`config.target_roles` × every searchable `config.ats_platforms` entry ×
every location in `locations.yaml` — with the past-24h time filter applied by
the search client (`tbs=qdr:d`). No hardcoded role/ATS lists: the matrix
follows whatever the config says.

A generated query looks like::

    site:boards.greenhouse.io "Backend Engineer" "San Francisco"

Only ATS that Phase 2 can actually parse (those with a `url_parser` regex)
have a `site:` domain here; config ATS without one are returned in the
`skipped_ats` list so the caller can log them rather than wasting queries on
results we couldn't extract.

`generate_queries` is pure (the optional shuffle takes an injected RNG), so
it's cheap to test and to preview counts before a run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from jobpulse.config import AppConfig

# Default locations file (repo root, next to config.yaml).
DEFAULT_LOCATIONS_PATH = Path(__file__).resolve().parent.parent.parent / "locations.yaml"

# jobhive ATS type → Google `site:` domain. Only ATS with a matching
# `url_parser` regex (so a discovered URL can be recognized + extracted) are
# listed; config ATS absent here are reported as skipped.
ATS_SITE_DOMAINS: dict[str, str] = {
    "greenhouse": "boards.greenhouse.io",
    "lever": "jobs.lever.co",
    "ashby": "jobs.ashbyhq.com",
    "icims": "careers.icims.com",
    "smartrecruiters": "jobs.smartrecruiters.com",
    "workable": "apply.workable.com",
    "rippling": "ats.rippling.com/careers",
    "gem": "jobs.gem.com",
    "workday": "myworkdayjobs.com",
}

# Schedule slots (SCOPE §7): which config ATS tiers and which location regions
# each daily cron run covers. Together the slots cover the full matrix once.
SLOT_PLAN: dict[str, dict[str, set[str]]] = {
    "morning": {"tiers": {"primary"}, "regions": {"usa"}},
    "afternoon": {"tiers": {"primary"}, "regions": {"india", "generic"}},
    "evening": {"tiers": {"secondary", "low_priority"}, "regions": {"usa", "india", "generic"}},
}

_REGION_ORDER = ("usa", "india", "generic")
_TIER_ORDER = ("primary", "secondary", "low_priority")


@dataclass
class AtsDomain:
    """An ATS resolved to its Google `site:` domain (for query building)."""

    key: str
    site: str


def load_locations(path: str | Path | None = None) -> dict[str, list[str]]:
    """Read ``locations.yaml`` into ``{region: [city, ...]}``.

    Missing regions default to empty lists so a partial file still works.
    """
    p = Path(path) if path is not None else DEFAULT_LOCATIONS_PATH
    with open(p) as f:
        raw = yaml.safe_load(f) or {}
    return {region: list(raw.get(region) or []) for region in _REGION_ORDER}


def build_query(role: str, domain: str, location: str | None = None) -> str:
    """Compose one Google search string: ``site:{domain} "{role}" "{location}"``."""
    q = f'site:{domain} "{role}"'
    if location:
        q += f' "{location}"'
    return q


def _platforms_for_tiers(config: AppConfig, tiers: set[str] | None) -> list[str]:
    """Config ATS platforms in the given tiers (all tiers when ``tiers`` is None)."""
    ats = config.ats_platforms
    by_tier = {
        "primary": ats.primary,
        "secondary": ats.secondary,
        "low_priority": ats.low_priority,
    }
    out: list[str] = []
    for tier in _TIER_ORDER:
        if tiers is None or tier in tiers:
            out.extend(by_tier[tier])
    return out


def generate_queries(
    config: AppConfig,
    locations: dict[str, list[str]],
    *,
    slot: str | None = None,
    regions: list[str] | None = None,
    shuffle: bool = False,
    rng: random.Random | None = None,
) -> tuple[list[str], list[str]]:
    """Generate queries from config, optionally restricted to one slot.

    Returns ``(queries, skipped_ats)`` where ``skipped_ats`` are config ATS in
    the selected tiers that have no `site:` domain (and would yield URLs we
    can't parse). With ``slot=None`` the full matrix is produced. ``regions``
    (e.g. ``config.google_search.regions``) further limits which location
    regions are searched — pass ``["usa", "generic"]`` to hold off India. When
    ``shuffle`` is set, the order is randomized with ``rng`` so a capped run /
    repeated clicks sample broadly instead of always re-issuing the first-N.
    """
    if slot is not None and slot not in SLOT_PLAN:
        raise ValueError(f"Unknown slot {slot!r}; expected one of {sorted(SLOT_PLAN)}")
    plan = SLOT_PLAN.get(slot) if slot else None
    tiers = plan["tiers"] if plan else None
    slot_regions = plan["regions"] if plan else None

    domains: list[AtsDomain] = []
    skipped: list[str] = []
    for ats in _platforms_for_tiers(config, tiers):
        site = ATS_SITE_DOMAINS.get(ats)
        if site is None:
            if ats not in skipped:
                skipped.append(ats)
        else:
            domains.append(AtsDomain(ats, site))

    # A region is searched only if the slot allows it AND it's in the configured
    # region scope (when given).
    region_keys = [
        r
        for r in _REGION_ORDER
        if (slot_regions is None or r in slot_regions)
        and (regions is None or r in regions)
    ]

    queries: list[str] = []
    for role in config.target_roles:
        for region in region_keys:
            for location in locations.get(region, []):
                for ats in domains:
                    queries.append(build_query(role, ats.site, location))

    if shuffle:
        (rng or random.Random()).shuffle(queries)
    return queries, skipped


def slot_counts(config: AppConfig, locations: dict[str, list[str]]) -> dict[str, int]:
    """Query count per slot — handy for budgeting before a run."""
    return {slot: len(generate_queries(config, locations, slot=slot)[0]) for slot in SLOT_PLAN}
