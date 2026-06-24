"""Google query generation for the Phase 2 channel (Module M2-1).

Turns role groups × ATS domains × locations into ``site:`` search strings,
applies the §7 optimization rules (skip combos unlikely to pay off), and
partitions the set across three daily schedule slots so each run stays
modest.

A generated query looks like::

    site:boards.greenhouse.io ("AI Engineer" OR "LLM Engineer" OR ...) "San Francisco"

Locations come from ``locations.yaml`` (repo root). The builder is pure —
no DB, no network — so it's cheap to test and to preview counts before a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Default locations file (repo root, next to config.yaml).
DEFAULT_LOCATIONS_PATH = Path(__file__).resolve().parent.parent.parent / "locations.yaml"

# Role groups (SCOPE §4). Each value is an OR-joined, pre-quoted phrase list
# that drops straight into the parenthesized clause of a query.
ROLE_GROUPS: dict[str, str] = {
    "swe": (
        '"Software Engineer" OR "Software Development Engineer" OR "SDE" '
        'OR "Backend Engineer" OR "Senior Software Engineer"'
    ),
    "ai": (
        '"AI Engineer" OR "Generative AI Engineer" OR "GenAI Engineer" '
        'OR "LLM Engineer" OR "Agentic AI Engineer" OR "Agent Engineer" '
        'OR "AI Applications Engineer"'
    ),
    "founding": (
        '"Forward Deployed Engineer" OR "Founding Engineer" '
        'OR "Founding Software Engineer" OR "Founding AI Engineer"'
    ),
}


@dataclass(frozen=True)
class AtsDomain:
    key: str
    site: str  # the host(/path) after the `site:` operator
    tier: str  # "primary" | "secondary" | "low"


# ATS domains and their site: operators (SCOPE §5), in priority order.
ATS_DOMAINS: tuple[AtsDomain, ...] = (
    AtsDomain("greenhouse", "boards.greenhouse.io", "primary"),
    AtsDomain("ashby", "jobs.ashbyhq.com", "primary"),
    AtsDomain("lever", "jobs.lever.co", "primary"),
    AtsDomain("icims", "careers.icims.com", "primary"),
    AtsDomain("smartrecruiters", "jobs.smartrecruiters.com", "secondary"),
    AtsDomain("workable", "apply.workable.com", "secondary"),
    AtsDomain("wellfound", "wellfound.com/company", "secondary"),
    AtsDomain("workatastartup", "workatastartup.com", "secondary"),
    AtsDomain("oracle", "careers.oracle.com", "secondary"),
    AtsDomain("rippling", "ats.rippling.com/careers", "secondary"),
    AtsDomain("gem", "jobs.gem.com", "secondary"),
    AtsDomain("workday", "myworkdayjobs.com", "low"),
)

# Schedule slots (SCOPE §7): which ATS tiers and which location regions each
# daily run covers. Together the three slots cover every combination once.
SLOT_PLAN: dict[str, dict[str, set[str]]] = {
    "morning": {"tiers": {"primary"}, "regions": {"usa"}},
    "afternoon": {"tiers": {"primary"}, "regions": {"india", "generic"}},
    "evening": {"tiers": {"secondary", "low"}, "regions": {"usa", "india", "generic"}},
}


def load_locations(path: str | Path | None = None) -> dict[str, list[str]]:
    """Read ``locations.yaml`` into ``{region: [city, ...]}``.

    Missing regions default to empty lists so a partial file still works.
    """
    p = Path(path) if path is not None else DEFAULT_LOCATIONS_PATH
    with open(p) as f:
        raw = yaml.safe_load(f) or {}
    return {region: list(raw.get(region) or []) for region in ("usa", "india", "generic")}


def build_query(role_group: str, ats: AtsDomain, location: str) -> str:
    """Compose one Google search string."""
    return f'site:{ats.site} ({ROLE_GROUPS[role_group]}) "{location}"'


def applicable_role_groups(ats_key: str, region: str) -> list[str]:
    """Role groups worth running for an ATS/region (SCOPE §7 skip rules).

    - Startup boards (Wellfound, WorkAtAStartup) skip generic SWE.
    - Oracle skips Founding (it doesn't hire 'founding engineers').
    - India locations skip Founding (those roles are predominantly US-based).
    """
    groups = list(ROLE_GROUPS.keys())  # swe, ai, founding
    if ats_key in {"wellfound", "workatastartup"} and "swe" in groups:
        groups.remove("swe")
    if ats_key == "oracle" and "founding" in groups:
        groups.remove("founding")
    if region == "india" and "founding" in groups:
        groups.remove("founding")
    return groups


def generate_queries(
    locations: dict[str, list[str]],
    *,
    slot: str | None = None,
    ats_domains: tuple[AtsDomain, ...] = ATS_DOMAINS,
) -> list[str]:
    """Generate query strings, optionally restricted to one schedule slot.

    With ``slot=None`` every combination is produced (all slots' union).
    """
    if slot is not None and slot not in SLOT_PLAN:
        raise ValueError(f"Unknown slot {slot!r}; expected one of {sorted(SLOT_PLAN)}")
    plan = SLOT_PLAN.get(slot) if slot else None
    tiers = plan["tiers"] if plan else None
    regions = plan["regions"] if plan else None

    queries: list[str] = []
    for ats in ats_domains:
        if tiers is not None and ats.tier not in tiers:
            continue
        for region in ("usa", "india", "generic"):
            if regions is not None and region not in regions:
                continue
            groups = applicable_role_groups(ats.key, region)
            for location in locations.get(region, []):
                for group in groups:
                    queries.append(build_query(group, ats, location))
    return queries


def slot_counts(locations: dict[str, list[str]]) -> dict[str, int]:
    """Query count per slot — handy for budgeting before a run."""
    return {slot: len(generate_queries(locations, slot=slot)) for slot in SLOT_PLAN}
