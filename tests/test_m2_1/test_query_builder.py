"""M2-1 (reworked) — config-driven query generation."""

from __future__ import annotations

import random

import pytest

from jobpulse.config import ATSPlatforms
from jobpulse.google_search.query_builder import (
    ATS_SITE_DOMAINS,
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


def _config_with_ats(test_config, *, primary, secondary=None, low=None, roles=None):
    update = {"ats_platforms": ATSPlatforms(primary=primary, secondary=secondary or [], low_priority=low or [])}
    if roles is not None:
        update["target_roles"] = roles
    return test_config.model_copy(update=update)


# ── build_query ────────────────────────────────────────────────────────────


def test_build_query_format():
    assert build_query("Backend Engineer", "boards.greenhouse.io", "San Francisco") == (
        'site:boards.greenhouse.io "Backend Engineer" "San Francisco"'
    )


def test_build_query_without_location():
    assert build_query("AI Engineer", "jobs.lever.co") == 'site:jobs.lever.co "AI Engineer"'


# ── config-driven generation ───────────────────────────────────────────────


def test_uses_config_roles_and_ats(test_config):
    cfg = _config_with_ats(test_config, primary=["greenhouse", "lever"], roles=["AI Engineer"])
    queries, skipped = generate_queries(cfg, LOCS)
    assert skipped == []
    # One query per role × ats × location (1 role × 2 ats × 4 locations = 8).
    assert len(queries) == 1 * 2 * 4
    assert 'site:boards.greenhouse.io "AI Engineer" "San Francisco"' in queries
    assert 'site:jobs.lever.co "AI Engineer" "Bangalore"' in queries
    # Only configured roles/domains appear.
    assert all('"AI Engineer"' in q for q in queries)


def test_unsupported_ats_skipped_and_absent(test_config):
    cfg = _config_with_ats(
        test_config, primary=["greenhouse"], secondary=["jazzhr", "teamtailor", "smartrecruiters"]
    )
    queries, skipped = generate_queries(cfg, LOCS)
    # jazzhr/teamtailor have no site: domain → reported, never queried.
    assert set(skipped) == {"jazzhr", "teamtailor"}
    assert not any("jazzhr" in q or "teamtailor" in q for q in queries)
    # smartrecruiters IS supported → present.
    assert any("jobs.smartrecruiters.com" in q for q in queries)


def test_all_four_unsupported_ats(test_config):
    cfg = _config_with_ats(
        test_config, primary=["greenhouse"], secondary=["jazzhr", "teamtailor", "bamboohr", "phenom"]
    )
    _, skipped = generate_queries(cfg, LOCS)
    assert set(skipped) == {"jazzhr", "teamtailor", "bamboohr", "phenom"}


# ── slots ──────────────────────────────────────────────────────────────────


def test_morning_is_primary_usa(test_config):
    cfg = _config_with_ats(test_config, primary=["greenhouse"], secondary=["smartrecruiters"], roles=["AI Engineer"])
    q, _ = generate_queries(cfg, LOCS, slot="morning")
    assert q  # non-empty
    assert all("boards.greenhouse.io" in x for x in q)  # primary only
    assert not any("Bangalore" in x for x in q)  # usa only
    assert not any("smartrecruiters" in x for x in q)  # secondary excluded


def test_slots_partition_full_set(test_config):
    cfg = _config_with_ats(
        test_config, primary=["greenhouse"], secondary=["smartrecruiters"], low=["workday"], roles=["AI Engineer"]
    )
    full = set(generate_queries(cfg, LOCS)[0])
    union, total = set(), 0
    for slot in SLOT_PLAN:
        s = generate_queries(cfg, LOCS, slot=slot)[0]
        total += len(s)
        union |= set(s)
    assert union == full
    assert total == len(full)  # disjoint slots, no overlap


def test_invalid_slot_raises(test_config):
    with pytest.raises(ValueError):
        generate_queries(test_config, LOCS, slot="midnight")


def test_regions_filter_holds_off_india(test_config):
    cfg = _config_with_ats(test_config, primary=["greenhouse"], roles=["AI Engineer"])
    # Default scope excludes India.
    q, _ = generate_queries(cfg, LOCS, regions=["usa", "generic"])
    assert any('"San Francisco"' in x for x in q)
    assert any('"Remote"' in x for x in q)
    assert not any('"Bangalore"' in x for x in q)  # India held off
    # Opting India back in restores it.
    q_in, _ = generate_queries(cfg, LOCS, regions=["usa", "india", "generic"])
    assert any('"Bangalore"' in x for x in q_in)


def test_default_config_regions_exclude_india(test_config):
    assert "india" not in test_config.google_search.regions


# ── shuffle ────────────────────────────────────────────────────────────────


def test_shuffle_preserves_set_changes_order(test_config):
    cfg = _config_with_ats(test_config, primary=["greenhouse", "lever"], roles=["AI Engineer", "Backend Engineer"])
    ordered, _ = generate_queries(cfg, LOCS)
    shuffled, _ = generate_queries(cfg, LOCS, shuffle=True, rng=random.Random(1))
    assert set(shuffled) == set(ordered)
    assert shuffled != ordered  # order differs (enough queries to be near-certain)


def test_shuffle_is_seed_deterministic(test_config):
    cfg = _config_with_ats(test_config, primary=["greenhouse", "lever"], roles=["AI Engineer", "Backend Engineer"])
    a, _ = generate_queries(cfg, LOCS, shuffle=True, rng=random.Random(7))
    b, _ = generate_queries(cfg, LOCS, shuffle=True, rng=random.Random(7))
    assert a == b


# ── invariants ─────────────────────────────────────────────────────────────


def test_every_site_domain_has_url_pattern():
    """Every ATS we build a site: query for must be recognizable by url_parser."""
    assert set(ATS_SITE_DOMAINS) <= set(_PATTERNS)


def test_slot_counts_real_config(test_config):
    counts = slot_counts(test_config, load_locations())
    assert set(counts) == set(SLOT_PLAN)
