"""M1-7 — end-to-end Phase 2 pipeline with mocked search + fetch."""

from __future__ import annotations

import sqlite3

import httpx

from jobpulse.google_search import pipeline as gs_pipeline
from jobpulse.google_search.rate_limiter import RateLimiter
from jobpulse.google_search.search_client import CaptchaError


class FakeClient:
    """Stands in for GoogleSearchClient: maps each query to result URLs."""

    def __init__(self, results: dict[str, list[str]] | list[str], *, raises=None):
        self._results = results
        self._raises = raises
        self.calls: list[str] = []

    def search(self, query: str) -> list[str]:
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        if isinstance(self._results, dict):
            return self._results.get(query, [])
        return self._results

    def close(self):  # pragma: no cover - parity with real client
        pass


# Job-page fetch responses keyed by a substring of the requested URL.
def _router(routes: dict[str, httpx.Response]):
    def fetch(url: str) -> httpx.Response:
        for needle, resp in routes.items():
            if needle in url:
                return resp
        return httpx.Response(404, text="not found")

    return fetch


GH_URL = "https://boards.greenhouse.io/anthropic/jobs/12345"
LV_URL = "https://jobs.lever.co/palantir/abc-def-123"

GH_BODY = {
    "title": "Software Engineer",
    "company_name": "Anthropic",
    "location": {"name": "San Francisco, CA"},
    "content": "Build things",
}
LV_BODY = {
    "text": "Backend Engineer",
    "categories": {"location": "New York"},
    "descriptionPlain": "Backend work",
}


def _no_delay_limiter(max_queries=100):
    return RateLimiter(max_queries=max_queries, sleep=lambda _s: None)


def _run(test_config, **kw):
    kw.setdefault("rate_limiter", _no_delay_limiter())
    return gs_pipeline.run_google_search_pipeline(test_config, **kw)


# ── Happy path ────────────────────────────────────────────────────────────


def test_end_to_end_inserts_jobs(test_db: sqlite3.Connection, test_config):
    client = FakeClient([GH_URL, LV_URL])
    fetch = _router({
        "boards-api.greenhouse.io": httpx.Response(200, json=GH_BODY),
        "api.lever.co": httpx.Response(200, json=LV_BODY),
    })
    out = _run(test_config, queries=["q"], search_client=client, fetch=fetch)

    assert out["status"] == "success"
    assert out["urls_found"] == 2
    assert out["urls_new"] == 2
    assert out["jobs_inserted"] == 2

    rows = test_db.execute(
        "SELECT global_id, source, url FROM jobs ORDER BY global_id"
    ).fetchall()
    assert {r["global_id"] for r in rows} == {"greenhouse:12345", "lever:abc-def-123"}
    assert all(r["source"] == "google_search" for r in rows)


def test_search_run_recorded(test_db: sqlite3.Connection, test_config):
    client = FakeClient([GH_URL])
    fetch = _router({"boards-api.greenhouse.io": httpx.Response(200, json=GH_BODY)})
    _run(test_config, queries=["q"], schedule_slot="manual", search_client=client, fetch=fetch)

    run = test_db.execute("SELECT * FROM search_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["schedule_slot"] == "manual"
    assert run["queries_executed"] == 1
    assert run["urls_found"] == 1
    assert run["jobs_inserted"] == 1
    assert run["status"] == "success"


# ── Dedup ─────────────────────────────────────────────────────────────────


def test_dedup_against_existing_global_id(test_db: sqlite3.Connection, test_config, seed):
    # Phase 1 already stored this posting.
    seed(test_db, global_id="greenhouse:12345", source="jobhive")
    client = FakeClient([GH_URL])
    fetch = _router({"boards-api.greenhouse.io": httpx.Response(200, json=GH_BODY)})
    out = _run(test_config, queries=["q"], search_client=client, fetch=fetch)

    assert out["jobs_skipped_dedup"] == 1
    assert out["jobs_inserted"] == 0
    # Still only the one (jobhive) row.
    n = test_db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE global_id='greenhouse:12345'"
    ).fetchone()["c"]
    assert n == 1


def test_cache_prevents_refetch_second_run(test_db: sqlite3.Connection, test_config):
    fetch = _router({"boards-api.greenhouse.io": httpx.Response(200, json=GH_BODY)})
    _run(test_config, queries=["q"], search_client=FakeClient([GH_URL]), fetch=fetch)
    # Second run, same query → URL is cached → skipped before dedup/extract.
    out2 = _run(test_config, queries=["q"], search_client=FakeClient([GH_URL]), fetch=fetch)
    assert out2["urls_new"] == 0
    assert out2["jobs_inserted"] == 0


# ── Filtering ─────────────────────────────────────────────────────────────


def test_out_of_region_job_filtered(test_db: sqlite3.Connection, test_config):
    foreign = {**GH_BODY, "location": {"name": "Bangalore, India"}}
    fetch = _router({"boards-api.greenhouse.io": httpx.Response(200, json=foreign)})
    out = _run(test_config, queries=["q"], search_client=FakeClient([GH_URL]), fetch=fetch)
    assert out["jobs_inserted"] == 0
    assert test_db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0


def test_unrecognized_url_skipped(test_db: sqlite3.Connection, test_config):
    client = FakeClient(["https://example.com/jobs/1", GH_URL])
    fetch = _router({"boards-api.greenhouse.io": httpx.Response(200, json=GH_BODY)})
    out = _run(test_config, queries=["q"], search_client=client, fetch=fetch)
    assert out["urls_found"] == 2
    assert out["jobs_inserted"] == 1  # only the greenhouse one


# ── Resilience ────────────────────────────────────────────────────────────


def test_captcha_marks_rate_limited(test_db: sqlite3.Connection, test_config):
    client = FakeClient([], raises=CaptchaError("blocked"))
    out = _run(test_config, queries=["q"], search_client=client, fetch=_router({}))
    assert out["status"] == "rate_limited"
    assert out["jobs_inserted"] == 0


def test_repeated_errors_are_deduped_in_run_log(test_db: sqlite3.Connection, test_config):
    from jobpulse.google_search.search_client import RateLimitedError

    # Every query hits the same 429 — error_msg should collapse, not repeat.
    client = FakeClient([], raises=RateLimitedError("Google returned HTTP 429"))
    _run(test_config, queries=["q1", "q2", "q3"], search_client=client, fetch=_router({}))

    msg = test_db.execute(
        "SELECT error_msg FROM search_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["error_msg"]
    assert msg == "Google returned HTTP 429 (×3)"
    assert "429; Google returned HTTP 429" not in msg  # not the repeated form


def test_query_budget_cap(test_db: sqlite3.Connection, test_config):
    client = FakeClient([])
    out = gs_pipeline.run_google_search_pipeline(
        test_config,
        queries=["q1", "q2", "q3"],
        search_client=client,
        fetch=_router({}),
        rate_limiter=RateLimiter(max_queries=1, sleep=lambda _s: None),
    )
    assert out["queries_executed"] == 1
    assert out["status"] == "partial"
    assert client.calls == ["q1"]


class FakeBrowserClient:
    """Search client with a fetch_html capability (browser engine path)."""

    def __init__(self, results, pages):
        self._results = results
        self._pages = pages
        self.fetched: list[str] = []

    def search(self, query):
        return list(self._results)

    def fetch_html(self, url):
        self.fetched.append(url)
        return self._pages.get(url)

    def close(self):
        pass


def test_browser_engine_extracts_from_tab_html(test_db: sqlite3.Connection, test_config):
    import json

    ld = {
        "@type": "JobPosting",
        "title": "Software Engineer",
        "hiringOrganization": {"name": "Anthropic"},
        "jobLocation": {"address": {"addressLocality": "San Francisco", "addressRegion": "CA"}},
    }
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    client = FakeBrowserClient([GH_URL], {GH_URL: page})
    out = _run(test_config, queries=["q"], search_client=client, fetch=lambda _u: None)

    assert out["jobs_inserted"] == 1
    assert client.fetched == [GH_URL]  # extraction went through the browser tab
    row = test_db.execute(
        "SELECT title, location, source FROM jobs WHERE global_id='greenhouse:12345'"
    ).fetchone()
    assert row["title"] == "Software Engineer"
    assert row["source"] == "google_search"


def test_page2_block_still_ingests_page1_jobs(test_db: sqlite3.Connection, test_config):
    """End-to-end: page 2 gets CAPTCHA'd, page 1's job still lands in the DB."""
    import json

    from jobpulse.google_search.browser_client import BrowserSearchClient

    ld = {
        "@type": "JobPosting",
        "title": "Software Engineer",
        "hiringOrganization": {"name": "Acme"},
        "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX"}},
    }
    job_page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    page1 = (
        '<a href="https://boards.greenhouse.io/acme/jobs/1">x</a>'
        '<a id="pnnext" href="/search?start=10">Next</a>'  # page 2 exists
    )
    captcha = "<html>Our systems have detected unusual traffic</html>"

    class _Tab:
        def __init__(self, html, url):
            self._html, self.url = html, url

        async def get_content(self):
            return self._html

        async def close(self):
            pass

    class _Browser:
        def __init__(self):
            self.opened = []

        async def get(self, url, new_tab=False, **_k):
            self.opened.append(url)
            if "start=10" in url:
                html = captcha            # page 2 → blocked
            elif "boards.greenhouse.io/acme/jobs/1" in url:
                html = job_page           # the result's job page (opened in a tab)
            else:
                html = page1              # page 1 results
            return _Tab(html, url)

        def stop(self):
            pass

    client = BrowserSearchClient(
        settle_seconds=0, page_delay_min=0, page_delay_max=0, tab_settle_seconds=0, max_pages=2
    )
    client._browser = _Browser()
    try:
        out = gs_pipeline.run_google_search_pipeline(
            test_config, queries=["q"], search_client=client,
            fetch=lambda _u: None, rate_limiter=_no_delay_limiter(),
        )
    finally:
        client.close()

    # Page 1's job is in the DB even though page 2 was rate-limited.
    assert out["jobs_inserted"] == 1
    row = test_db.execute(
        "SELECT title FROM jobs WHERE global_id='greenhouse:1'"
    ).fetchone()
    assert row is not None and row["title"] == "Software Engineer"


def test_concurrent_run_skipped(test_config, monkeypatch):
    # Simulate an in-progress run by holding the lock.
    assert gs_pipeline._search_lock.acquire(blocking=False)
    try:
        out = gs_pipeline.run_google_search_pipeline(
            test_config, queries=["q"], search_client=FakeClient([]), fetch=_router({})
        )
        assert out["status"] == "skipped"
    finally:
        gs_pipeline._search_lock.release()
