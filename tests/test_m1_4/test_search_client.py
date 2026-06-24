"""M1-4 — Google search client: HTML parsing + HTTP layer.

The HTTP layer is exercised with ``httpx.MockTransport`` (no network, no
extra deps); parsing is tested directly against the saved fixture.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jobpulse.google_search.search_client import (
    CaptchaError,
    GoogleSearchClient,
    RateLimitedError,
    detect_block,
    parse_result_urls,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "google_results.html").read_text()


def _client(handler) -> GoogleSearchClient:
    transport = httpx.MockTransport(handler)
    return GoogleSearchClient(client=httpx.Client(transport=transport))


# ── parse_result_urls (pure) ──────────────────────────────────────────────


def test_parse_extracts_result_urls():
    urls = parse_result_urls(FIXTURE)
    assert urls == [
        "https://boards.greenhouse.io/anthropic/jobs/12345",
        "https://jobs.lever.co/palantir/abc-def-123",
        "https://jobs.ashbyhq.com/OpenAI/Some-Slug",
    ]


def test_parse_dedupes_and_drops_google_chrome():
    urls = parse_result_urls(FIXTURE)
    # The duplicated greenhouse result collapses to one.
    assert urls.count("https://boards.greenhouse.io/anthropic/jobs/12345") == 1
    # No google.com / policies / accounts links leak through.
    assert all("google.com" not in u for u in urls)


def test_parse_empty_html():
    assert parse_result_urls("<html></html>") == []


# ── search() over MockTransport ───────────────────────────────────────────


def test_search_returns_parsed_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["tbs"] == "qdr:d"
        assert "q" in request.url.params
        return httpx.Response(200, text=FIXTURE)

    with _client(handler) as client:
        urls = client.search('site:boards.greenhouse.io "Software Engineer"')
    assert "https://boards.greenhouse.io/anthropic/jobs/12345" in urls


def test_search_raises_on_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many requests")

    with _client(handler) as client, pytest.raises(RateLimitedError):
        client.search("anything")


def test_search_raises_on_captcha_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>Our systems have detected unusual traffic</html>"
        )

    with _client(handler) as client, pytest.raises(CaptchaError):
        client.search("anything")


def test_detect_block_on_sorry_url():
    # A response whose final URL is the /sorry/ interstitial (post-redirect).
    resp = httpx.Response(
        200,
        text="<html>ok</html>",
        request=httpx.Request("GET", "https://www.google.com/sorry/index?continue=x"),
    )
    with pytest.raises(CaptchaError):
        detect_block(resp)


def test_detect_block_passes_clean_response():
    resp = httpx.Response(
        200, text=FIXTURE, request=httpx.Request("GET", "https://www.google.com/search")
    )
    detect_block(resp)  # no raise


def test_user_agent_rotates():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text=FIXTURE)

    with _client(handler) as client:
        for _ in range(5):
            client.search("q")
    # Pool has 4 UAs; over 5 calls we should see more than one distinct UA
    # and the pool should have cycled (first == fifth).
    assert len(set(seen)) > 1
    assert seen[0] == seen[4]
