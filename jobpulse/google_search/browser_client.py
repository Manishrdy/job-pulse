"""Google search via the real system Chrome — no webdriver (anti-rate-limit).

Plain HTTP search now reliably trips Google's ``/sorry/`` + HTTP 429 bot
defense (see logs). This client instead drives the **actual installed
Chrome** through `nodriver`, which speaks Chrome's debugging protocol directly
and omits the automation fingerprints (no chromedriver, no Selenium) that
Google flags — the same approach as the role-collector reference.

`nodriver` is async; the pipeline is synchronous, so this client owns a
dedicated event loop and exposes the same blocking ``search(query) -> [url]``
/ ``close()`` interface as :class:`~jobpulse.google_search.search_client.GoogleSearchClient`.
One Chrome instance is launched lazily and reused for the whole run.

Result HTML is parsed by the existing :func:`parse_result_urls`; CAPTCHA /
"unusual traffic" pages raise :class:`CaptchaError` so the rate limiter backs
off, exactly like the HTTP path.

``nodriver`` is imported lazily so the module (and the test suite) load fine
on machines without it installed; only ``search()`` needs it.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote_plus

from jobpulse.google_search.search_client import (
    _CAPTCHA_MARKERS,
    CaptchaError,
    parse_result_urls,
)

log = logging.getLogger(__name__)

# Extra CAPTCHA wording seen on the interactive (JS) results page.
_BROWSER_CAPTCHA_MARKERS = (*_CAPTCHA_MARKERS, "type the characters", "not a robot")


class BrowserSearchClient:
    """Runs Google searches in the real Chrome via nodriver (sync interface)."""

    def __init__(
        self,
        *,
        headless: bool = False,
        settle_seconds: float = 3.0,
        num_results: int = 20,
    ) -> None:
        self._headless = headless
        self._settle = settle_seconds
        self._num = num_results
        self._loop = asyncio.new_event_loop()
        self._browser = None  # nodriver.Browser, started lazily on first search

    def _build_url(self, query: str) -> str:
        return (
            "https://www.google.com/search?"
            f"q={quote_plus(query)}&num={self._num}&hl=en&tbs=qdr:d"
        )

    def _detect_block(self, html: str, url: str) -> None:
        if "/sorry/" in (url or "") or "sorry.google.com" in (url or ""):
            raise CaptchaError(f"Google served a /sorry/ interstitial: {url}")
        low = html.lower()
        if any(marker in low for marker in _BROWSER_CAPTCHA_MARKERS):
            raise CaptchaError("Google served a CAPTCHA / unusual-traffic page")

    async def _ensure_browser(self):
        if self._browser is None:
            import nodriver as uc

            self._browser = await uc.start(headless=self._headless)
            log.info("Launched Chrome via nodriver (headless=%s)", self._headless)
        return self._browser

    async def _search_async(self, query: str) -> list[str]:
        browser = await self._ensure_browser()
        tab = await browser.get(self._build_url(query))
        await asyncio.sleep(self._settle)  # let results render
        html = await tab.get_content()
        self._detect_block(html, getattr(tab, "url", "") or "")
        return parse_result_urls(html)

    def search(self, query: str) -> list[str]:
        """Run one Google search in real Chrome, return organic result URLs."""
        urls = self._loop.run_until_complete(self._search_async(query))
        log.info("Browser search %r → %d result URLs", query, len(urls))
        return urls

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.stop()  # nodriver Browser.stop() is synchronous
        except Exception:
            log.warning("Failed to stop nodriver browser", exc_info=True)
        finally:
            self._browser = None
            if not self._loop.is_closed():
                self._loop.close()

    def __enter__(self) -> BrowserSearchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
