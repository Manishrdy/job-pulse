"""M2-1 — config-driven query generation (Workday=per-city, rest=United States)."""

from __future__ import annotations

import random

import pytest

from jobpulse.config import ATSPlatforms
from jobpulse.google_search.query_builder import (
    BROAD_LOCATION,
    SLOT_PLAN,
    build_query,
    generate_queries,
    load_locations,
    slot_counts,
)
from jobpulse.google_search.url_parser import _PATTERNS

LOCS = {
    "usa": ["San Francisco", "Austin"],
    "india": ["Bangalore"],
    "generic": ["Remote"],
}


def _cfg(test_config, *, primary, secondary=None, low=None, roles=None):
    update = {"ats_platforms": ATSPlatforms(primary=primary, secondary=secondary or [], low_priority=low or [])}
    if roles is not None:
        update["target_roles"] = roles
    return test_config.model_copy(update=update)


# ── build_query ────────────────────────────────────────────────────────────


def test_build_query_format():
    assert build_query("Backend Engineer", "boards.greenhouse.io", "United States") == (
        'site:boards.greenhouse.io "Backend Engineer" "United States"'
    )


# ── location strategy ──────────────────────────────────────────────────────


def test_non_workday_uses_united_states_only(test_config):
    cfg = _cfg(test_config, primary=["greenhouse", "lever"], roles=["AI Engineer"])
    q, _ = generate_queries(cfg, LOCS)
    # One broad query per (role × ats) — no city names.
    assert len(q) == 2  # 1 role × 2 ats × 1 location
    assert all(f'"{BROAD_LOCATION}"' in x for x in q)
    assert not any("San Francisco" in x or "Austin" in x for x in q)


def test_workday_searches_per_city(test_config):
    cfg = _cfg(test_config, primary=["greenhouse"], low=["workday"], roles=["AI Engineer"])
    q, _ = generate_queries(cfg, LOCS)
    workday = [x for x in q if "myworkdayjobs.com" in x]
    greenhouse = [x for x in q if "boards.greenhouse.io" in x]
    # Workday → one query per US city; greenhouse → single United States query.
    assert sorted(workday) == [
        'site:myworkdayjobs.com "AI Engineer" "Austin"',
        'site:myworkdayjobs.com "AI Engineer" "San Francisco"',
    ]
    assert greenhouse == ['site:boards.greenhouse.io "AI Engineer" "United States"']


def test_workday_cities_come_from_usa_region_only(test_config):
    cfg = _cfg(test_config, primary=["workday"], roles=["AI Engineer"])
    q, _ = generate_queries(cfg, LOCS)
    assert all("myworkdayjobs.com" in x for x in q)
    assert not any("Bangalore" in x for x in q)  # india region not searched
    assert not any("Remote" in x for x in q)     # generic not used for cities


# ── skip rules ─────────────────────────────────────────────────────────────


def test_unsupported_ats_skipped(test_config):
    cfg = _cfg(test_config, primary=["greenhouse"], secondary=["jazzhr", "teamtailor", "smartrecruiters"])
    q, skipped = generate_queries(cfg, LOCS)
    assert set(skipped) == {"jazzhr", "teamtailor"}
    assert not any("jazzhr" in x or "teamtailor" in x for x in q)
    assert any("jobs.smartrecruiters.com" in x for x in q)


def test_all_four_unsupported_ats(test_config):
    cfg = _cfg(test_config, primary=["greenhouse"], secondary=["jazzhr", "teamtailor", "bamboohr", "phenom"])
    _, skipped = generate_queries(cfg, LOCS)
    assert set(skipped) == {"jazzhr", "teamtailor", "bamboohr", "phenom"}


# ── slots (tier-based, disjoint) ───────────────────────────────────────────


def test_morning_is_primary_only(test_config):
    cfg = _cfg(test_config, primary=["greenhouse"], secondary=["smartrecruiters"], low=["workday"], roles=["AI Engineer"])
    q, _ = generate_queries(cfg, LOCS, slot="morning")
    assert q == ['site:boards.greenhouse.io "AI Engineer" "United States"']


def test_slots_partition_full_set(test_config):
    cfg = _cfg(
        test_config, primary=["greenhouse"], secondary=["smartrecruiters"], low=["workday"], roles=["AI Engineer"]
    )
    full = set(generate_queries(cfg, LOCS)[0])
    union, total = set(), 0
    for slot in SLOT_PLAN:
        s = generate_queries(cfg, LOCS, slot=slot)[0]
        total += len(s)
        union |= set(s)
    assert union == full
    assert total == len(full)  # disjoint tiers


def test_invalid_slot_raises(test_config):
    with pytest.raises(ValueError):
        generate_queries(test_config, LOCS, slot="midnight")


# ── shuffle ────────────────────────────────────────────────────────────────


def test_shuffle_preserves_set_changes_order(test_config):
    cfg = _cfg(test_config, primary=["workday"], roles=["AI Engineer", "Backend Engineer"])
    big_locs = {"usa": [f"City{i}" for i in range(20)], "india": [], "generic": []}
    ordered, _ = generate_queries(cfg, big_locs)
    shuffled, _ = generate_queries(cfg, big_locs, shuffle=True, rng=random.Random(1))
    assert set(shuffled) == set(ordered)
    assert shuffled != ordered


def test_shuffle_is_seed_deterministic(test_config):
    cfg = _cfg(test_config, primary=["workday"], roles=["AI Engineer", "Backend Engineer"])
    big_locs = {"usa": [f"City{i}" for i in range(20)], "india": [], "generic": []}
    a, _ = generate_queries(cfg, big_locs, shuffle=True, rng=random.Random(7))
    b, _ = generate_queries(cfg, big_locs, shuffle=True, rng=random.Random(7))
    assert a == b


# ── invariants ─────────────────────────────────────────────────────────────


def test_every_site_domain_has_url_pattern():
    from jobpulse.google_search.query_builder import ATS_SITE_DOMAINS

    assert set(ATS_SITE_DOMAINS) <= set(_PATTERNS)


def test_slot_counts_real_config(test_config):
    counts = slot_counts(test_config, load_locations())
    assert set(counts) == set(SLOT_PLAN)


def test_load_real_locations():
    locs = load_locations()
    assert "San Francisco" in locs["usa"]
    assert "India" not in locs["generic"]  # US-only generic
