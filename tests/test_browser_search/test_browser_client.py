"""nodriver browser search client — URL build, block detection, parsing.

Uses a fake nodriver Browser/Tab so no real Chrome is launched. Live Chrome
behavior is a manual step (can't drive a GUI browser in CI).
"""

from __future__ import annotations

import pytest

from jobpulse.google_search.browser_client import BrowserSearchClient
from jobpulse.google_search.search_client import CaptchaError

RESULT_HTML = (
    '<html><body><div class="g">'
    '<a href="/url?q=https://boards.greenhouse.io/acme/jobs/1&sa=U"><h3>SWE</h3></a>'
    '</div>'
    '<a href="https://jobs.lever.co/acme/abc">Lead</a>'
    '<a href="https://www.google.com/preferences">Settings</a>'
    '</body></html>'
)


class _FakeTab:
    def __init__(self, html: str, url: str):
        self._html = html
        self.url = url

    async def get_content(self) -> str:
        return self._html


class _FakeBrowser:
    def __init__(self, html: str, url: str = ""):
        self._html = html
        self._url = url
        self.stopped = False

    async def get(self, url: str) -> _FakeTab:
        return _FakeTab(self._html, self._url or url)

    def stop(self) -> None:
        self.stopped = True


def _client_with(browser, **kw) -> BrowserSearchClient:
    c = BrowserSearchClient(settle_seconds=0, **kw)
    c._browser = browser  # skip real nodriver launch
    return c


# ── URL building ───────────────────────────────────────────────────────────


def test_build_url_has_time_and_params():
    c = BrowserSearchClient(settle_seconds=0, num_results=20)
    url = c._build_url('site:jobs.lever.co "AI Engineer" "Austin"')
    assert url.startswith("https://www.google.com/search?")
    assert "tbs=qdr:d" in url      # past 24h
    assert "num=20" in url
    assert "hl=en" in url
    assert "q=site%3Ajobs.lever.co" in url  # url-encoded query
    c.close()


# ── search() over a fake browser ───────────────────────────────────────────


def test_search_returns_parsed_urls():
    c = _client_with(_FakeBrowser(RESULT_HTML))
    try:
        urls = c.search('site:jobs.lever.co "AI Engineer"')
    finally:
        c.close()
    assert "https://boards.greenhouse.io/acme/jobs/1" in urls
    assert "https://jobs.lever.co/acme/abc" in urls
    assert all("google.com" not in u for u in urls)  # chrome links dropped


def test_close_stops_browser():
    browser = _FakeBrowser(RESULT_HTML)
    c = _client_with(browser)
    c.search("q")
    c.close()
    assert browser.stopped


# ── block detection ────────────────────────────────────────────────────────


def test_captcha_html_raises():
    c = _client_with(_FakeBrowser("<html>Our systems have detected unusual traffic</html>"))
    with pytest.raises(CaptchaError):
        c.search("q")
    c.close()


def test_type_the_characters_raises():
    c = _client_with(_FakeBrowser("<html>To continue, please type the characters below</html>"))
    with pytest.raises(CaptchaError):
        c.search("q")
    c.close()


def test_sorry_url_raises():
    c = _client_with(_FakeBrowser("<html>ok</html>", url="https://www.google.com/sorry/index?continue=x"))
    with pytest.raises(CaptchaError):
        c.search("q")
    c.close()


# ── engine factory ─────────────────────────────────────────────────────────


def test_factory_selects_engine(test_config):
    from jobpulse.config import GoogleSearch
    from jobpulse.google_search.pipeline import _make_search_client
    from jobpulse.google_search.search_client import GoogleSearchClient

    browser_cfg = test_config.model_copy(update={"google_search": GoogleSearch(engine="browser")})
    http_cfg = test_config.model_copy(update={"google_search": GoogleSearch(engine="http")})

    bc = _make_search_client(browser_cfg)
    try:
        assert isinstance(bc, BrowserSearchClient)
    finally:
        bc.close()

    hc = _make_search_client(http_cfg)
    try:
        assert isinstance(hc, GoogleSearchClient)
    finally:
        hc.close()


def test_default_engine_is_browser():
    from jobpulse.config import GoogleSearch

    assert GoogleSearch().engine == "browser"
