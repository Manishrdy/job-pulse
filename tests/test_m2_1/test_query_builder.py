"""M2-1 — query builder: format, skip rules, slot partitioning, budget."""

from __future__ import annotations

import pytest

from jobpulse.google_search.query_builder import (
    ROLE_GROUPS,
    SLOT_PLAN,
    AtsDomain,
    applicable_role_groups,
    build_query,
    generate_queries,
    load_locations,
    slot_counts,
)

LOCS = {
    "usa": ["San Francisco", "Austin"],
    "india": ["Bangalore"],
    "generic": ["Remote"],
}

GH = AtsDomain("greenhouse", "boards.greenhouse.io", "primary")


# ── build_query ────────────────────────────────────────────────────────────


def test_build_query_format():
    q = build_query("ai", GH, "San Francisco")
    assert q == f'site:boards.greenhouse.io ({ROLE_GROUPS["ai"]}) "San Francisco"'
    assert q.startswith("site:boards.greenhouse.io (")
    assert q.endswith('"San Francisco"')


# ── skip rules (§7) ────────────────────────────────────────────────────────


def test_startup_boards_skip_swe():
    assert "swe" not in applicable_role_groups("wellfound", "usa")
    assert "swe" not in applicable_role_groups("workatastartup", "usa")
    assert "ai" in applicable_role_groups("wellfound", "usa")


def test_oracle_skips_founding():
    assert "founding" not in applicable_role_groups("oracle", "usa")


def test_india_skips_founding():
    assert "founding" not in applicable_role_groups("greenhouse", "india")
    assert "founding" in applicable_role_groups("greenhouse", "usa")


def test_default_groups_are_all_three():
    assert applicable_role_groups("greenhouse", "usa") == ["swe", "ai", "founding"]


# ── generate_queries ───────────────────────────────────────────────────────


def test_generate_all_combos():
    qs = generate_queries(LOCS)
    # Every query is well-formed and unique.
    assert all(q.startswith("site:") for q in qs)
    assert len(qs) == len(set(qs))


def test_skip_rules_reflected_in_output():
    qs = generate_queries(LOCS)
    # No wellfound SWE queries.
    wf_swe = [q for q in qs if "wellfound.com/company" in q and ROLE_GROUPS["swe"] in q]
    assert wf_swe == []
    # No founding queries for Bangalore.
    founding_blr = [q for q in qs if ROLE_GROUPS["founding"] in q and "Bangalore" in q]
    assert founding_blr == []


def test_slots_partition_the_full_set():
    """The three slots together cover exactly the full set, no overlap."""
    full = set(generate_queries(LOCS))
    slot_union: set[str] = set()
    total = 0
    for slot in SLOT_PLAN:
        s = generate_queries(LOCS, slot=slot)
        total += len(s)
        slot_union |= set(s)
    assert slot_union == full
    assert total == len(full)  # disjoint slots


def test_morning_is_primary_usa_only():
    qs = generate_queries(LOCS, slot="morning")
    assert all('"San Francisco"' in q or '"Austin"' in q for q in qs)
    assert all("boards.greenhouse.io" in q or "ashbyhq" in q or "lever" in q or "icims" in q for q in qs)
    assert not any("Bangalore" in q for q in qs)


def test_invalid_slot_raises():
    with pytest.raises(ValueError):
        generate_queries(LOCS, slot="midnight")


def test_slot_counts_keys():
    counts = slot_counts(LOCS)
    assert set(counts) == set(SLOT_PLAN)
    assert all(c > 0 for c in counts.values())


# ── real locations.yaml ────────────────────────────────────────────────────


def test_load_real_locations():
    locs = load_locations()
    assert len(locs["usa"]) > 40
    assert "San Francisco" in locs["usa"]
    assert "Bangalore" in locs["india"]
    assert "Remote" in locs["generic"]


def test_primary_slots_within_budget():
    """Morning + afternoon (the primary-ATS slots) stay under ~700/run."""
    counts = slot_counts(load_locations())
    assert counts["morning"] <= 700
    assert counts["afternoon"] <= 700
