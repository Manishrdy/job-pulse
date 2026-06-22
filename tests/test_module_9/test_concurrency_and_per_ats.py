from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from jobpulse import pipeline
from jobpulse.config import AppConfig
from jobpulse.ingest import record_scrape_run, record_scrape_run_ats
from jobpulse.scraper import AtsScrape, ScrapeResult, run_scrape
from tests.conftest import make_jobhive_job, seed_job


def _config(tmp_path: Path, primary: list[str], concurrency: int = 8) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": primary},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
        scrape={"max_companies_per_ats": None, "concurrency": concurrency},
    )


# --- ScrapeResult backward-compat ------------------------------------------


def test_scrape_result_aggregate_properties():
    r = ScrapeResult(
        ats_results=[
            AtsScrape(ats="greenhouse", fetched=10, jobs=["a", "b"]),
            AtsScrape(ats="lever", fetched=5, jobs=["c"]),
        ]
    )
    assert r.ats_types == ["greenhouse", "lever"]
    assert r.total_fetched == 15
    assert r.jobs == ["a", "b", "c"]


# --- Concurrent run_scrape -------------------------------------------------


def test_concurrent_scrape_correct_counts(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\n" + "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(20)) + "\n"
    )
    config = _config(tmp_path, ["greenhouse"], concurrency=8)

    def fake(ats, ident):
        return [
            make_jobhive_job(title="Software Engineer", ats_id=f"{ident}-m"),
            make_jobhive_job(title="Marketing Manager", ats_id=f"{ident}-s"),
        ]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    assert result.total_fetched == 40           # 20 companies x 2
    assert len(result.jobs) == 20               # only matches
    assert len(result.ats_results) == 1
    assert result.ats_results[0].fetched == 40


def test_concurrency_actually_parallel(tmp_path: Path):
    # 8 companies each sleeping; with concurrency >=8 they overlap, proving
    # the pool runs them in parallel (max observed in-flight > 1).
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\n" + "\n".join(f"C{i},c{i},https://e.com/c{i}" for i in range(8)) + "\n"
    )
    config = _config(tmp_path, ["greenhouse"], concurrency=8)

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    barrier = threading.Barrier(8, timeout=5)

    def fake(ats, ident):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            barrier.wait()  # all 8 must be in-flight simultaneously
        except threading.BrokenBarrierError:
            pass
        with lock:
            in_flight -= 1
        return []

    run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    assert max_in_flight >= 2  # genuinely concurrent (barrier proves all 8)


def test_priority_order_preserved_across_ats(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text("name,slug,url\nA,a,https://e.com/a\n")
    (tmp_path / "lever.csv").write_text("name,slug,url\nB,b,https://e.com/b\n")
    config = _config(tmp_path, ["greenhouse", "lever"])

    seen_ats = []

    def fake(ats, ident):
        seen_ats.append(ats)
        return []

    run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    # greenhouse (primary, listed first) before lever
    assert seen_ats == ["greenhouse", "lever"]


def test_error_contained_per_ats(tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text(
        "name,slug,url\nGood,good,https://e.com/good\nBad,bad,https://e.com/bad\n"
    )
    config = _config(tmp_path, ["greenhouse"])

    def fake(ats, ident):
        if ident == "bad":
            raise RuntimeError("boom")
        return [make_jobhive_job(title="Software Engineer", ats_id="ok")]

    result = run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    assert len(result.jobs) == 1
    assert len(result.errors) == 1
    assert result.ats_results[0].errors == 1


# --- Per-ATS logging -------------------------------------------------------


def test_record_scrape_run_ats(test_db: sqlite3.Connection):
    run_id = record_scrape_run(
        test_db, schedule_slot="morning", ats_types_scraped=["greenhouse", "lever"],
        jobs_fetched=100, jobs_inserted=10, jobs_updated=5, jobs_blocked=2, status="success",
    )
    record_scrape_run_ats(test_db, run_id, [
        {"ats_type": "greenhouse", "fetched": 70, "inserted": 7, "updated": 3, "blocked": 1, "errors": 0},
        {"ats_type": "lever", "fetched": 30, "inserted": 3, "updated": 2, "blocked": 1, "errors": 1},
    ])
    rows = test_db.execute(
        "SELECT * FROM scrape_run_ats WHERE run_id = ? ORDER BY ats_type", (run_id,)
    ).fetchall()
    assert len(rows) == 2
    gh = [r for r in rows if r["ats_type"] == "greenhouse"][0]
    assert gh["jobs_fetched"] == 70 and gh["jobs_inserted"] == 7 and gh["jobs_blocked"] == 1


def test_jobs_blocked_column_exists(test_db: sqlite3.Connection):
    cols = {r[1] for r in test_db.execute("PRAGMA table_info(scrape_runs)")}
    assert "jobs_blocked" in cols


def test_scrape_run_ats_table_exists(test_db: sqlite3.Connection):
    t = test_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scrape_run_ats'"
    ).fetchone()
    assert t is not None


# --- Pipeline records per-ATS + blocked ------------------------------------


def test_pipeline_records_per_ats(test_db: sqlite3.Connection, test_config, tmp_path: Path):
    (tmp_path / "greenhouse.csv").write_text("name,slug,url\nAcme,acme,https://e.com/acme\n")
    (tmp_path / "lever.csv").write_text("name,slug,url\nBeta,beta,https://e.com/beta\n")
    # test_config primary is [greenhouse, lever]; block Beta's company first.
    test_db.execute("INSERT INTO company_blocklist (company) VALUES ('Beta')")
    test_db.commit()

    def fake(ats, ident):
        return [make_jobhive_job(title="Software Engineer", ats_id=f"{ats}-{ident}")]

    result = pipeline.run_scrape_pipeline(
        test_config, schedule_slot="morning", scrape_fn=fake, manifest_dir=str(tmp_path)
    )
    assert "per_ats" in result
    assert result["blocked"] == 1   # the Beta job

    run = test_db.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["jobs_blocked"] == 1
    ats_rows = test_db.execute(
        "SELECT * FROM scrape_run_ats WHERE run_id = ?", (run["id"],)
    ).fetchall()
    by_ats = {r["ats_type"]: r for r in ats_rows}
    assert by_ats["greenhouse"]["jobs_inserted"] == 1
    assert by_ats["lever"]["jobs_blocked"] == 1


# --- UI breakdown ----------------------------------------------------------


def test_scrape_logs_shows_per_ats_breakdown(client: TestClient, test_db: sqlite3.Connection):
    run_id = record_scrape_run(
        test_db, schedule_slot="morning", ats_types_scraped=["greenhouse"],
        jobs_fetched=50, jobs_inserted=5, jobs_updated=2, jobs_blocked=1, status="success",
    )
    record_scrape_run_ats(test_db, run_id, [
        {"ats_type": "greenhouse", "fetched": 50, "inserted": 5, "updated": 2, "blocked": 1, "errors": 0},
    ])
    html = client.get("/scrape-logs").text
    assert "Blocked" in html          # new column header
    assert "Per-ATS" in html          # breakdown column
    assert "mini-table" in html       # expandable per-ATS table rendered
    assert "1 ATS" in html            # summary count
