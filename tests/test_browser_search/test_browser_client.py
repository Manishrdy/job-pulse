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
        self.closed = False

    async def get_content(self) -> str:
        return self._html

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    """Returns ``html`` for any get; or a per-URL map when ``pages`` is given."""

    def __init__(self, html: str = "", url: str = "", *, pages: dict[str, str] | None = None):
        self._html = html
        self._url = url
        self._pages = pages or {}
        self.stopped = False
        self.opened: list[str] = []

    async def get(self, url: str, new_tab: bool = False, **_kw) -> _FakeTab:
        self.opened.append(url)
        html = self._html
        for needle, page_html in self._pages.items():
            if needle in url:
                html = page_html
                break
        return _FakeTab(html, self._url or url)

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
    assert "start=" not in url     # page 0 has no start
    c.close()


def test_build_url_pagination_start():
    c = BrowserSearchClient(settle_seconds=0)
    assert "start=10" in c._build_url("q", page=1)
    assert "start=20" in c._build_url("q", page=2)
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


# ── pagination ─────────────────────────────────────────────────────────────

_PAGE1 = (
    '<a href="https://boards.greenhouse.io/acme/jobs/1">A</a>'
    '<a id="pnnext" href="/search?start=10">Next</a>'  # page 2 exists
)
_PAGE2 = '<a href="https://jobs.lever.co/acme/two">B</a>'  # no pnnext → last page


def test_search_follows_page_two():
    browser = _FakeBrowser(pages={"start=10": _PAGE2, "google.com/search": _PAGE1})
    c = _client_with(browser, max_pages=2)
    try:
        urls = c.search("q")
    finally:
        c.close()
    assert "https://boards.greenhouse.io/acme/jobs/1" in urls   # page 1
    assert "https://jobs.lever.co/acme/two" in urls             # page 2
    assert sum("start=10" in u for u in browser.opened) == 1    # fetched page 2 once


def test_search_stops_when_no_next_page():
    browser = _FakeBrowser(pages={"google.com/search": _PAGE2})  # page 1 has no pnnext
    c = _client_with(browser, max_pages=3)
    try:
        c.search("q")
    finally:
        c.close()
    assert not any("start=10" in u for u in browser.opened)  # never fetched page 2


# ── fetch_html (per-result tab) ────────────────────────────────────────────


def test_fetch_html_opens_tab_returns_content():
    browser = _FakeBrowser("<html><title>Job</title></html>")
    c = _client_with(browser)
    try:
        html = c.fetch_html("https://boards.greenhouse.io/acme/jobs/1")
    finally:
        c.close()
    assert "<title>Job</title>" in html
    assert "boards.greenhouse.io/acme/jobs/1" in browser.opened[-1]


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


def test_default_uses_persistent_profile():
    from jobpulse.config import GoogleSearch

    # A persistent profile (not a per-run temp dir) is the default — this is what
    # keeps Google's trust cookies so CAPTCHA is solved once, not every run.
    assert GoogleSearch().user_data_dir  # non-empty


def test_client_records_user_data_dir():
    c = BrowserSearchClient(settle_seconds=0, user_data_dir="~/.jobpulse/chrome-profile")
    try:
        assert c._user_data_dir == "~/.jobpulse/chrome-profile"
    finally:
        c.close()
