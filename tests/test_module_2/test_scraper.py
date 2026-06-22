from __future__ import annotations

from pathlib import Path

import pytest

from jobpulse.config import AppConfig
from jobpulse.models import JobRecord
from jobpulse.scraper import (
    DEFAULT_MANIFEST_DIR,
    ats_priority_order,
    load_company_manifest,
    run_scrape,
    title_matches,
)
from tests.conftest import make_jobhive_job


def test_ats_priority_order(test_config: AppConfig):
    order = ats_priority_order(test_config)
    assert order == ["greenhouse", "lever", "smartrecruiters", "workday"]
    # primary platforms come strictly before secondary/low
    assert order.index("greenhouse") < order.index("smartrecruiters")
    assert order.index("smartrecruiters") < order.index("workday")


def test_title_matches():
    roles = ["Software Engineer", "AI Engineer"]
    assert title_matches("Senior Software Engineer", roles) is True
    assert title_matches("software engineer ii", roles) is True  # case-insensitive
    assert title_matches("Staff AI Engineer", roles) is True
    assert title_matches("Marketing Manager", roles) is False
    assert title_matches("", roles) is False


def test_load_company_manifest_real_greenhouse():
    entries = load_company_manifest("greenhouse")
    assert len(entries) > 0
    first = entries[0]
    assert first.name
    assert first.slug
    assert first.url


def test_load_company_manifest_missing_returns_empty(tmp_path: Path):
    assert load_company_manifest("nonexistent_ats", tmp_path) == []


def test_load_company_manifest_custom_dir(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nAcme Corp,acme,https://example.com/acme\n"
    )
    entries = load_company_manifest("greenhouse", tmp_path)
    assert len(entries) == 1
    assert entries[0].name == "Acme Corp"
    assert entries[0].slug == "acme"


def _build_config(tmp_path: Path, primary: list[str]) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": primary},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
    )


def test_run_scrape_priority_order_and_filtering(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nGH One,gh1,https://e.com/gh1\nGH Two,gh2,https://e.com/gh2\n"
    )
    (tmp_path / "lever.csv").write_text(
        "name,slug,url\nLever One,lv1,https://e.com/lv1\n"
    )
    config = AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse", "lever"]},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
    )

    call_order: list[tuple[str, str]] = []

    def fake_scrape(ats: str, identifier: str):
        call_order.append((ats, identifier))
        # Each company returns one matching + one non-matching job.
        return [
            make_jobhive_job(title="Software Engineer", ats_id=f"{identifier}-match"),
            make_jobhive_job(title="Marketing Manager", ats_id=f"{identifier}-skip"),
        ]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake_scrape)

    # 3 companies x 2 jobs each fetched
    assert result.total_fetched == 6
    # only the 3 matching jobs kept
    assert len(result.jobs) == 3
    assert all(isinstance(j, JobRecord) for j in result.jobs)
    assert all(j.title == "Software Engineer" for j in result.jobs)

    # greenhouse (primary first in list) scraped before lever
    ats_seen = [ats for ats, _ in call_order]
    assert ats_seen == ["greenhouse", "greenhouse", "lever"]
    # company display name carried from manifest
    assert {j.company for j in result.jobs} == {"GH One", "GH Two", "Lever One"}


def test_run_scrape_error_contained(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nGood,good,https://e.com/good\nBad,bad,https://e.com/bad\n"
    )
    config = _build_config(tmp_path, ["greenhouse"])

    def fake_scrape(ats: str, identifier: str):
        if identifier == "bad":
            raise RuntimeError("scraper exploded")
        return [make_jobhive_job(title="Software Engineer", ats_id="ok")]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake_scrape)

    assert len(result.jobs) == 1  # good company still processed
    assert len(result.errors) == 1
    assert "bad" in result.errors[0]


def test_run_scrape_workday_passes_url_not_slug(tmp_path: Path):
    (tmp_path / "workday.csv").write_text(
        "name,slug,url\n"
        "Acme,acme/external,https://acme.wd1.myworkdayjobs.com/external\n"
    )
    config = _build_config(tmp_path, ["workday"])

    received: list[str] = []

    def fake_scrape(ats: str, identifier: str):
        received.append(identifier)
        return []

    run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake_scrape)
    assert received == ["https://acme.wd1.myworkdayjobs.com/external"]


def test_run_scrape_max_companies_cap(tmp_path: Path):
    rows = "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(10))
    (tmp_path / "greenhouse.csv").write_text(f"name,slug,url\n{rows}\n")
    config = _build_config(tmp_path, ["greenhouse"])

    calls: list[str] = []

    def fake_scrape(ats: str, identifier: str):
        calls.append(identifier)
        return []

    run_scrape(config, manifest_dir=tmp_path, max_companies_per_ats=3, scrape_fn=fake_scrape)
    assert len(calls) == 3


def test_default_manifest_dir_exists():
    # Sanity: the vendored manifests are where scraper.py expects them.
    assert DEFAULT_MANIFEST_DIR.exists()
    assert (DEFAULT_MANIFEST_DIR / "greenhouse.csv").exists()
