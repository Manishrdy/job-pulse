from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jobpulse.config import AppConfig, Location
from jobpulse.location import (
    LocationMatch,
    classify_location,
    is_target_location,
    purge_non_target_location,
)
from jobpulse.scraper import run_scrape
from tests.conftest import make_jobhive_job, seed_job

US = LocationMatch.US
NON_US = LocationMatch.NON_US
UNKNOWN = LocationMatch.UNKNOWN


# --- classify: the user's exact leaked examples ----------------------------


@pytest.mark.parametrize(
    "loc",
    [
        "London, England, United Kingdom",
        "Hamburg or Berlin, de",
        "Burnaby, British Columbia, Canada",
        "London, HOLBEIN GARDENS, gb",
        "Berlin, Germany",
        "Toronto, ON, Canada",
        "Bengaluru, India",
        "Paris, France",
        "Remote, UK",
        "Sydney, Australia",
    ],
)
def test_foreign_locations_classified_non_us(loc):
    assert classify_location(loc) is NON_US


@pytest.mark.parametrize(
    "loc",
    [
        "San Francisco, CA",
        "New York, NY",
        "Austin, Texas",
        "Remote, US",
        "Boston, Massachusetts, United States",
        "Seattle, WA",
        "Remote - US",
        "London, KY",          # London, Kentucky — US wins via abbrev
        "Wilmington, DE",       # Delaware abbrev, not Germany
        "Indianapolis, Indiana",  # not 'India'
        # City + state with no "USA", in both abbrev and full-name forms:
        "Charlotte, NC",
        "Charlotte, North Carolina",
        "Raleigh-Durham, North Carolina",
        "Nashville, TN",        # Tennessee, not Tamil Nadu
        "Minneapolis, MN",      # Minnesota, not Manipur
        "Portland, OR",         # Oregon, not Odisha
        "New Orleans, LA",      # Louisiana, not Ladakh
        "Atlanta, GA",          # Georgia (US), not Goa
    ],
)
def test_us_locations_classified_us(loc):
    assert classify_location(loc) is US


@pytest.mark.parametrize(
    "loc",
    [
        # City-only listings (Adobe Workday patterns) — no state, still US:
        "San Jose",
        "Seattle",
        "San Francisco",
        "Greater Seattle Area",
        "US Remote",
        "Bay Area",
        "South San Francisco",
        "Lehi",
        "NYC",
    ],
)
def test_us_city_only_classified_us(loc):
    assert classify_location(loc) is US


def test_country_name_beats_city():
    # A US-named city with an explicit foreign country is dropped.
    assert classify_location("San Jose, Costa Rica") is NON_US
    assert classify_location("India - Remote") is NON_US


def test_state_roster_covers_all_us_states():
    from jobpulse.location import US_STATES
    # Every state resolves as US by both its code and its full name.
    for code, name in US_STATES.items():
        assert classify_location(f"Somecity, {code}") is US, code
        assert classify_location(f"Somecity, {name}") is US, name


@pytest.mark.parametrize("loc", ["Remote", "", None, "Multiple Locations", "Anywhere"])
def test_ambiguous_locations_unknown(loc):
    assert classify_location(loc) is UNKNOWN


def test_country_iso_is_authoritative():
    assert classify_location("Somewhere weird", country_iso="GB") is NON_US
    assert classify_location("Somewhere weird", country_iso="US") is US
    # ISO overrides even a US-looking string
    assert classify_location("New York, NY", country_iso="CA") is NON_US  # CA = Canada ISO here


def test_us_signal_wins_over_foreign():
    # A role open to both — US-eligible, so keep.
    assert classify_location("Remote - US or Canada") is US


# --- India roster (target switch) ------------------------------------------


def test_india_target_keeps_india_drops_us():
    # Indian states match by code and full name when India is the target.
    assert classify_location("Bengaluru, KA", country_code="IN") is US        # Karnataka
    assert classify_location("Pune, Maharashtra", country_code="IN") is US
    assert classify_location("Chennai, Tamil Nadu", country_code="IN") is US
    assert classify_location("Mumbai, India", country_code="IN") is US
    # US jobs are foreign when the target is India.
    assert classify_location("Austin, TX", country_code="IN") is NON_US
    assert classify_location("San Francisco, California", country_code="IN") is NON_US


def test_us_target_drops_india():
    # The user's concern: India jobs must not leak when the target is US.
    assert classify_location("Bengaluru, Karnataka") is NON_US
    assert classify_location("Chennai, Tamil Nadu") is NON_US   # TN here = Tamil Nadu
    assert classify_location("Pune, Maharashtra, India") is NON_US
    assert classify_location("Hyderabad, Telangana") is NON_US


# --- is_target_location policy ---------------------------------------------

STRICT = Location(country_code="US", remote_preferred=True, keep_unknown=False)
LENIENT = Location(country_code="US", remote_preferred=True, keep_unknown=True)


def test_policy_us_kept_foreign_dropped():
    assert is_target_location("Austin, TX", None, None, STRICT) is True
    assert is_target_location("London, United Kingdom", None, None, STRICT) is False


def test_policy_unknown_strict_keeps_only_remote():
    assert is_target_location("Remote", None, True, STRICT) is True       # remote → keep
    assert is_target_location(None, None, False, STRICT) is False         # unknown non-remote → drop


def test_policy_unknown_lenient_keeps():
    assert is_target_location(None, None, False, LENIENT) is True


# --- run_scrape integration -------------------------------------------------


def test_run_scrape_drops_foreign_keeps_us(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text("name,slug,url\nAcme,acme,https://e.com/acme\n")
    config = AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
        location={"country_code": "US", "keep_unknown": False, "remote_preferred": True},
    )

    def fake(ats, ident):
        return [
            make_jobhive_job(title="Software Engineer", ats_id="us", location="Austin, TX"),
            make_jobhive_job(title="Software Engineer", ats_id="uk", location="London, England, United Kingdom"),
            make_jobhive_job(title="Software Engineer", ats_id="de", location="Berlin, Germany"),
            make_jobhive_job(title="Software Engineer", ats_id="ca", location="Toronto, ON, Canada"),
        ]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    titles_locs = [(j.location) for j in result.jobs]
    assert titles_locs == ["Austin, TX"]   # only the US job survives
    assert result.total_fetched == 4       # all 4 still counted as fetched


# --- purge existing rows ----------------------------------------------------


def test_purge_removes_foreign_keeps_us_and_unknown(test_db: sqlite3.Connection):
    us = seed_job(test_db, location="San Francisco, CA")
    unknown = seed_job(test_db, location="Remote")
    uk = seed_job(test_db, location="London, England, United Kingdom")
    de = seed_job(test_db, location="Berlin, Germany", global_id="gh:de")

    deleted = purge_non_target_location(test_db, Location())
    assert deleted == 2
    remaining = {r["id"] for r in test_db.execute("SELECT id FROM jobs")}
    assert remaining == {us, unknown}
    assert uk not in remaining and de not in remaining
