"""Config-driven Google query generation for Phase 2 (Module M2-1).

Builds `site:` search strings from the user's **config** — every
`config.target_roles` × every searchable `config.ats_platforms` entry — with
the past-24h time filter applied by the search client (`tbs=qdr:d`).

**Location strategy (US-only):** most ATS list a role broadly, so searching
per city just multiplies near-identical queries and burns the rate limit —
they get a single ``"United States"`` query per role. **Workday** is the
exception: a Workday "company" is a huge enterprise tenant, so a city term
meaningfully narrows results — only Workday searches per US city
(``locations.yaml`` ``usa:``).

A generated query looks like::

    site:boards.greenhouse.io "Backend Engineer" "United States"
    site:myworkdayjobs.com "Backend Engineer" "San Francisco"   # Workday only

`generate_queries` is pure (the optional shuffle takes an injected RNG).
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

# The single broad location used for every non-city ATS.
BROAD_LOCATION = "United States"
# Only these ATS search per US city; everyone else uses BROAD_LOCATION.
CITY_SEARCH_ATS: set[str] = {"workday"}

# Schedule slots (cron): which config ATS tiers each daily run covers. Split by
# tier so the slots stay disjoint (location is no longer region-based).
SLOT_PLAN: dict[str, set[str]] = {
    "morning": {"primary"},
    "afternoon": {"secondary"},
    "evening": {"low_priority"},
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
    shuffle: bool = False,
    rng: random.Random | None = None,
) -> tuple[list[str], list[str]]:
    """Generate queries from config, optionally restricted to one slot.

    Returns ``(queries, skipped_ats)`` where ``skipped_ats`` are config ATS in
    the selected tiers that have no `site:` domain (and would yield URLs we
    can't parse). Workday searches per US city; every other ATS uses a single
    ``"United States"`` query per role. When ``shuffle`` is set, the order is
    randomized with ``rng`` so a capped run / repeated clicks sample broadly
    instead of always re-issuing the first-N.
    """
    if slot is not None and slot not in SLOT_PLAN:
        raise ValueError(f"Unknown slot {slot!r}; expected one of {sorted(SLOT_PLAN)}")
    tiers = SLOT_PLAN.get(slot) if slot else None

    domains: list[AtsDomain] = []
    skipped: list[str] = []
    for ats in _platforms_for_tiers(config, tiers):
        site = ATS_SITE_DOMAINS.get(ats)
        if site is None:
            if ats not in skipped:
                skipped.append(ats)
        else:
            domains.append(AtsDomain(ats, site))

    us_cities = locations.get("usa", [])

    # Two priority groups: every non-Workday ATS first, Workday last (it's the
    # lowest priority and its per-city queries are by far the most numerous, so
    # within a capped run we want the other ATS — Greenhouse, Ashby, Lever,
    # Rippling, iCIMS, … — covered before Workday gets any budget).
    other: list[str] = []
    workday: list[str] = []
    for ats in domains:
        if ats.key in CITY_SEARCH_ATS:
            bucket, locs = workday, us_cities
        else:
            bucket, locs = other, [BROAD_LOCATION]
        for role in config.target_roles:
            for location in locs:
                bucket.append(build_query(role, ats.site, location))

    if shuffle:
        r = rng or random.Random()
        r.shuffle(other)    # fair rotation across the non-Workday ATS
        r.shuffle(workday)
    # Workday always trails — only reached once the per-run cap has room left.
    return other + workday, skipped


def slot_counts(config: AppConfig, locations: dict[str, list[str]]) -> dict[str, int]:
    """Query count per slot — handy for budgeting before a run."""
    return {slot: len(generate_queries(config, locations, slot=slot)[0]) for slot in SLOT_PLAN}
