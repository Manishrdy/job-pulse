"""Per-company yield tracking + skip-the-unproductive behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jobpulse import pipeline
from jobpulse.company_yield import (
    CompanyYield,
    load_skip_set,
    record_company_yield,
    record_company_yields,
)
from jobpulse.config import AppConfig
from jobpulse.database import get_connection, init_db
from jobpulse.scraper import run_scrape
from tests.conftest import make_jobhive_job


def _row(conn: sqlite3.Connection, ats: str, slug: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM company_yield WHERE ats_type = ? AND slug = ?", (ats, slug)
    ).fetchone()


def _config(tmp_path: Path, **scrape) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
        location={"country_code": "US", "keep_unknown": False, "remote_preferred": True},
        scrape=scrape or {},
    )


# --- streak logic ----------------------------------------------------------


def test_region_hit_resets_streak(test_db: sqlite3.Connection):
    # Two reachable-but-foreign runs build a streak...
    record_company_yield(test_db, "greenhouse", CompanyYield("acme", "Acme", fetched=5, region_count=0))
    record_company_yield(test_db, "greenhouse", CompanyYield("acme", "Acme", fetched=5, region_count=0))
    assert _row(test_db, "greenhouse", "acme")["unproductive_streak"] == 2
    # ...then one in-region job resets it and stamps last_region_at.
    record_company_yield(test_db, "greenhouse", CompanyYield("acme", "Acme", fetched=5, region_count=1))
    row = _row(test_db, "greenhouse", "acme")
    assert row["unproductive_streak"] == 0
    assert row["region_jobs_total"] == 1
    assert row["reachable_runs"] == 3
    assert row["runs"] == 3
    assert row["last_region_at"] is not None


def test_zero_fetch_never_grows_streak(test_db: sqlite3.Connection):
    # A run that returns nothing (lull / dead slug) must not count toward skip.
    record_company_yield(test_db, "greenhouse", CompanyYield("ghost", "Ghost", fetched=0, region_count=0))
    record_company_yield(test_db, "greenhouse", CompanyYield("ghost", "Ghost", fetched=0, region_count=0))
    row = _row(test_db, "greenhouse", "ghost")
    assert row["unproductive_streak"] == 0
    assert row["reachable_runs"] == 0
    assert row["runs"] == 2


# --- skip set --------------------------------------------------------------


def test_skip_set_includes_streak_within_cooldown(test_db: sqlite3.Connection):
    for _ in range(3):
        record_company_yield(test_db, "greenhouse", CompanyYield("foreign", "Foreign", 5, 0))
    test_db.commit()
    skip = load_skip_set(test_db, skip_after_runs=3, recheck_days=30)
    assert ("greenhouse", "foreign") in skip


def test_skip_set_excludes_below_threshold(test_db: sqlite3.Connection):
    for _ in range(2):
        record_company_yield(test_db, "greenhouse", CompanyYield("foreign", "Foreign", 5, 0))
    test_db.commit()
    assert load_skip_set(test_db, skip_after_runs=3, recheck_days=30) == set()


def test_skip_set_reprobes_after_cooldown(test_db: sqlite3.Connection):
    for _ in range(3):
        record_company_yield(test_db, "greenhouse", CompanyYield("foreign", "Foreign", 5, 0))
    # Age the last scrape past the re-probe window.
    test_db.execute(
        "UPDATE company_yield SET last_scraped_at = '2000-01-01T00:00:00Z' "
        "WHERE ats_type = 'greenhouse' AND slug = 'foreign'"
    )
    test_db.commit()
    assert load_skip_set(test_db, skip_after_runs=3, recheck_days=30) == set()


# --- run_scrape integration ------------------------------------------------


def test_run_scrape_honors_skip_predicate(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nKeep,keep,https://e.com/keep\nDrop,drop,https://e.com/drop\n"
    )
    config = _config(tmp_path)
    seen: list[str] = []

    def fake(ats, ident):
        seen.append(ident)
        return [make_jobhive_job(title="Software Engineer", location="Austin, TX")]

    def skip(ats, entry):
        return entry.slug == "drop"

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake, skip_company=skip)
    assert seen == ["keep"]                       # dropped company never fetched
    assert result.ats_results[0].skipped == 1
    assert {y[1].slug for y in result.ats_results[0].yields} == {"keep"}


def test_run_scrape_collects_region_count(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text("name,slug,url\nAcme,acme,https://e.com/acme\n")
    config = _config(tmp_path)

    def fake(ats, ident):
        return [
            make_jobhive_job(title="Software Engineer", ats_id="us", location="Austin, TX"),
            make_jobhive_job(title="Product Manager", ats_id="us2", location="Boston, MA"),
            make_jobhive_job(title="Software Engineer", ats_id="uk", location="London, United Kingdom"),
        ]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    ats = result.ats_results[0]
    # 2 US jobs (region-only, role-agnostic), 1 role-matched record kept.
    assert ats.yields[0][1].region_count == 2
    assert ats.yields[0][1].fetched == 3
    assert [j.location for j in result.jobs] == ["Austin, TX"]


def test_pipeline_skips_foreign_company_after_threshold(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nForeign,foreign,https://e.com/foreign\nUSco,usco,https://e.com/usco\n"
    )
    config = _config(
        tmp_path,
        max_companies_per_ats=None,
        skip_unproductive=True,
        skip_after_runs=2,
        recheck_days=30,
    )
    init_db(config).close()

    def fake(ats, ident):
        if ident == "foreign":
            return [make_jobhive_job(title="Software Engineer", location="Berlin, Germany")]
        return [make_jobhive_job(title="Software Engineer", location="Austin, TX")]

    seen_per_run: list[set[str]] = []

    def make_fake():
        seen: set[str] = set()
        seen_per_run.append(seen)

        def f(ats, ident):
            seen.add(ident)
            return fake(ats, ident)

        return f

    # Two runs: foreign returns only a German job (region_count 0, reachable).
    for _ in range(2):
        pipeline.run_scrape_pipeline(config, scrape_fn=make_fake(), manifest_dir=str(tmp_path))
    assert seen_per_run[0] == seen_per_run[1] == {"foreign", "usco"}

    conn = get_connection(config.database.path)
    try:
        assert _row(conn, "greenhouse", "foreign")["unproductive_streak"] == 2
        assert load_skip_set(conn, skip_after_runs=2, recheck_days=30) == {("greenhouse", "foreign")}
    finally:
        conn.close()

    # Third run: foreign is skipped (never fetched); usco still scraped.
    pipeline.run_scrape_pipeline(config, scrape_fn=make_fake(), manifest_dir=str(tmp_path))
    assert seen_per_run[2] == {"usco"}

    conn = get_connection(config.database.path)
    try:
        ats_row = conn.execute(
            "SELECT * FROM scrape_run_ats ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert ats_row["companies_skipped"] == 1
    finally:
        conn.close()


def test_record_company_yields_bulk_commits(test_db: sqlite3.Connection):
    record_company_yields(
        test_db,
        [
            ("greenhouse", CompanyYield("a", "A", 3, 1)),
            ("lever", CompanyYield("b", "B", 0, 0)),
        ],
    )
    assert _row(test_db, "greenhouse", "a")["region_jobs_total"] == 1
    assert _row(test_db, "lever", "b")["runs"] == 1


# --- companies_service views -----------------------------------------------


def _seed_yield_mix(conn: sqlite3.Connection) -> None:
    # foreign: reachable, 3 foreign runs in a row
    for _ in range(3):
        record_company_yield(conn, "greenhouse", CompanyYield("foreign", "Foreign Co", 5, 0))
    # zero-but-not-skipped: reachable, only 1 foreign run
    record_company_yield(conn, "lever", CompanyYield("lull", "Lull Co", 4, 0))
    # productive
    record_company_yield(conn, "ashby", CompanyYield("usco", "US Co", 6, 2))
    # never reachable — must not appear in foreign/zero
    record_company_yield(conn, "icims", CompanyYield("ghost", "Ghost Co", 0, 0))
    conn.commit()


def test_companies_service_views(test_db: sqlite3.Connection):
    from jobpulse.services import companies_service

    _seed_yield_mix(test_db)
    cnt = companies_service.counts(test_db, skip_after_runs=3)
    assert cnt["foreign"] == 1            # only the 3x-foreign reachable co
    assert cnt["zero"] == 2               # foreign + lull (both reachable, 0 region)
    assert cnt["productive"] == 1         # usco
    assert cnt["all"] == 4

    foreign = companies_service.list_companies(test_db, view="foreign", skip_after_runs=3)
    assert [c["slug"] for c in foreign] == ["foreign"]
    assert foreign[0]["is_skipped"] == 1

    productive = companies_service.list_companies(test_db, view="productive", skip_after_runs=3)
    assert productive[0]["slug"] == "usco"
    assert productive[0]["is_skipped"] == 0


def test_companies_service_empty_when_no_table(tmp_path: Path):
    from jobpulse.services import companies_service

    # A bare DB without the company_yield table must not error.
    conn = get_connection(tmp_path / "bare.db")
    try:
        assert companies_service.counts(conn, skip_after_runs=3) == {
            "foreign": 0, "zero": 0, "productive": 0, "all": 0
        }
        assert companies_service.list_companies(conn, view="foreign", skip_after_runs=3) == []
    finally:
        conn.close()


def test_companies_page_renders(client, test_db: sqlite3.Connection):
    _seed_yield_mix(test_db)
    resp = client.get("/companies?view=foreign")
    assert resp.status_code == 200
    assert "Foreign Co" in resp.text
    assert "US Co" not in resp.text          # productive co not in the foreign view
    # Bad view falls back to foreign, still 200.
    assert client.get("/companies?view=bogus").status_code == 200
