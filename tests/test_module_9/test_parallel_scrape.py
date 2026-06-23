"""Parallel-ATS execution: dynamic worker distribution, real parallelism,
per-ATS concurrency config, and thread-safe concurrent ingest."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from jobpulse import pipeline
from jobpulse.config import AppConfig, Scrape
from jobpulse.database import get_connection, init_db
from jobpulse.scraper import distribute_workers, run_scrape
from tests.conftest import make_jobhive_job


def _config(tmp_path: Path, primary: list[str], **scrape) -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": primary},
        database={"path": str(tmp_path / "x.db")},
        logging={"file": str(tmp_path / "x.log")},
        scrape={"max_companies_per_ats": None, **scrape},
    )


def _manifest(tmp_path: Path, ats: str, n: int) -> None:
    rows = "\n".join(f"C{i},{ats}{i},https://e.com/{ats}{i}" for i in range(n))
    (tmp_path / f"{ats}.csv").write_text(f"name,slug,url\n{rows}\n")


# --- distribute_workers (pure) ---------------------------------------------


def test_distribute_proportional_split():
    alloc = distribute_workers({"a": 10, "b": 10}, lambda _a: 8, budget=20)
    assert alloc == {"a": 8, "b": 8}  # each clamped to ceiling 8, sum 16 <= 20


def test_distribute_clamps_to_ceiling():
    # workable dominates the company count but must never exceed its ceiling of 2.
    ceiling = lambda a: 2 if a == "workable" else 8  # noqa: E731
    alloc = distribute_workers({"workable": 1000, "greenhouse": 10}, ceiling, budget=20)
    assert alloc["workable"] == 2
    assert alloc["greenhouse"] >= 1


def test_distribute_no_idle_workers_for_tiny_ats():
    # 2 companies must get at most 2 workers even with a fat budget.
    assert distribute_workers({"a": 2}, lambda _a: 8, budget=20) == {"a": 2}


def test_distribute_sum_within_budget_when_trimmed():
    alloc = distribute_workers({"a": 10, "b": 10, "c": 10}, lambda _a: 8, budget=8)
    assert sum(alloc.values()) == 8


def test_distribute_more_ats_than_budget_each_gets_one():
    counts = {f"ats{i}": 5 for i in range(30)}
    alloc = distribute_workers(counts, lambda _a: 8, budget=20)
    assert all(v == 1 for v in alloc.values())  # everyone still scraped


def test_distribute_empty_and_all_zero():
    assert distribute_workers({}, lambda _a: 8, budget=20) == {}
    assert distribute_workers({"a": 0, "b": 0}, lambda _a: 8, budget=20) == {}


# --- per-ATS concurrency config --------------------------------------------


def test_concurrency_for_override_and_default():
    s = Scrape(default_ats_concurrency=8, per_ats_concurrency={"workable": 2})
    assert s.concurrency_for("workable") == 2
    assert s.concurrency_for("greenhouse") == 8


# --- real parallelism across ATS -------------------------------------------


def test_ats_run_in_parallel(tmp_path: Path):
    # 2 companies on each of 2 ATS. A barrier sized to ALL 4 only releases if
    # companies from BOTH ATS are in-flight at once — impossible if ATS were
    # processed sequentially (it would deadlock to the timeout).
    _manifest(tmp_path, "greenhouse", 2)
    _manifest(tmp_path, "lever", 2)
    config = _config(tmp_path, ["greenhouse", "lever"], concurrency=8)

    seen_ats: set[str] = set()
    released: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4, timeout=5)

    def fake(ats, ident):
        with lock:
            seen_ats.add(ats)
        try:
            barrier.wait()
            with lock:
                released.append(ident)
        except threading.BrokenBarrierError:
            pass
        return []

    run_scrape(config, manifest_dir=tmp_path, scrape_fn=fake)
    assert len(released) == 4            # all 4 were genuinely in-flight together
    assert seen_ats == {"greenhouse", "lever"}


# --- thread-safe concurrent ingest -----------------------------------------


def test_parallel_ingest_no_lost_or_double_writes(tmp_path: Path):
    _manifest(tmp_path, "greenhouse", 25)
    _manifest(tmp_path, "lever", 25)
    config = _config(tmp_path, ["greenhouse", "lever"], concurrency=8)
    init_db(config).close()

    def fake(ats, ident):
        time.sleep(0.001)  # force interleaving of concurrent commits
        return [make_jobhive_job(title="Software Engineer", ats_id=ident)]

    out = pipeline.run_scrape_pipeline(config, scrape_fn=fake, manifest_dir=str(tmp_path))
    assert out["status"] == "success"
    assert out["inserted"] == 50  # 50 distinct companies, one job each

    conn = get_connection(config.database.path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 50
    finally:
        conn.close()
