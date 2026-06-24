"""Google Search HTTP client — no-driver (Module M1-4).

Plain HTTP, no browser. Google's no-JS results page lists each hit as an
``<a href="/url?q=REAL_URL&...">`` anchor; we GET the page with a rotated
desktop User-Agent and the ``tbs=qdr:d`` ("past 24 hours") filter, then pull
the real result URLs out of that HTML.

Parsing is split from fetching (:func:`parse_result_urls` is pure) so it can
be tested against saved fixture HTML without ever hitting Google.

Bot-defense responses are surfaced as typed errors so the pipeline can back
off instead of treating a CAPTCHA page as "zero results":

- HTTP 429 → :class:`RateLimitedError`
- a ``/sorry/`` interstitial or "unusual traffic" body → :class:`CaptchaError`
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.google.com/search"

# Rotated per request. Real, current desktop Chrome UA strings — a small pool
# is enough to avoid a single static fingerprint at manual/low volume.
DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
)

# Hosts whose links are Google's own chrome (nav, account, policies, caches),
# never a search result. Matched as a suffix of the host.
_SKIP_HOST_SUFFIXES = (
    "google.com",
    "google.co",
    "gstatic.com",
    "googleusercontent.com",
    "youtube.com",
)

_CAPTCHA_MARKERS = (
    "unusual traffic",
    "/sorry/",
    "recaptcha",
    "detected unusual",
)


class SearchError(Exception):
    """Base for Google search client failures."""


class RateLimitedError(SearchError):
    """Google returned HTTP 429."""


class CaptchaError(SearchError):
    """Google served a CAPTCHA / 'unusual traffic' interstitial."""


def _is_google_host(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in _SKIP_HOST_SUFFIXES)


def parse_result_urls(html: str) -> list[str]:
    """Extract organic result URLs from a Google results page (pure).

    Handles both the ``/url?q=...`` redirect form and bare ``http(s)://``
    hrefs. Google's own hosts are dropped. Order is preserved and duplicates
    removed.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/url?"):
            q = parse_qs(urlsplit(href).query).get("q", [])
            if not q:
                continue
            href = q[0]
        if not href.startswith(("http://", "https://")):
            continue
        host = urlsplit(href).netloc
        if not host or _is_google_host(host):
            continue
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


def detect_block(response: httpx.Response) -> None:
    """Raise the appropriate error if ``response`` is a bot-defense page."""
    if response.status_code == 429:
        raise RateLimitedError("Google returned HTTP 429")
    if "/sorry/" in str(response.url):
        raise CaptchaError(f"Google served a /sorry/ interstitial: {response.url}")
    body = response.text.lower()
    if any(marker in body for marker in _CAPTCHA_MARKERS):
        raise CaptchaError("Google served a CAPTCHA / unusual-traffic page")


class GoogleSearchClient:
    """Fetches and parses Google search results over plain HTTP."""

    def __init__(
        self,
        *,
        user_agents: tuple[str, ...] | list[str] | None = None,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
        num_results: int = 20,
    ) -> None:
        self._user_agents = tuple(user_agents) if user_agents else DEFAULT_USER_AGENTS
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None
        self._num_results = num_results
        self._ua_index = 0

    def _next_user_agent(self) -> str:
        ua = self._user_agents[self._ua_index % len(self._user_agents)]
        self._ua_index += 1
        return ua

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._next_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def search(self, query: str) -> list[str]:
        """Run one Google search, return organic result URLs.

        Raises :class:`RateLimitedError` / :class:`CaptchaError` on bot
        defense so the caller can back off.
        """
        params = {"q": query, "tbs": "qdr:d", "num": str(self._num_results)}
        response = self._client.get(SEARCH_URL, params=params, headers=self._headers())
        detect_block(response)
        response.raise_for_status()
        urls = parse_result_urls(response.text)
        log.info("Google search %r → %d result URLs", query, len(urls))
        return urls

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GoogleSearchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
