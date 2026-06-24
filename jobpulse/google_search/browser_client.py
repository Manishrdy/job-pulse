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
import os
import random
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
        user_data_dir: str | None = None,
        max_pages: int = 2,
        tab_settle_seconds: float = 2.0,
        page_delay_min: float = 8.0,
        page_delay_max: float = 20.0,
    ) -> None:
        self._headless = headless
        self._settle = settle_seconds
        self._num = num_results
        self._user_data_dir = user_data_dir
        self._max_pages = max(1, max_pages)
        self._tab_settle = tab_settle_seconds
        self._page_delay_min = page_delay_min
        self._page_delay_max = page_delay_max
        self._loop = asyncio.new_event_loop()
        self._browser = None  # nodriver.Browser, started lazily on first search

    def _page_delay(self) -> float:
        if self._page_delay_max <= 0:
            return 0.0
        return random.uniform(self._page_delay_min, self._page_delay_max)

    def _build_url(self, query: str, page: int = 0) -> str:
        url = (
            "https://www.google.com/search?"
            f"q={quote_plus(query)}&num={self._num}&hl=en&tbs=qdr:d"
        )
        if page:
            url += f"&start={page * 10}"
        return url

    @staticmethod
    def _has_next_page(html: str) -> bool:
        # Google's "Next" pagination control.
        return 'id="pnnext"' in html or 'aria-label="Next page"' in html

    def _detect_block(self, html: str, url: str) -> None:
        if "/sorry/" in (url or "") or "sorry.google.com" in (url or ""):
            raise CaptchaError(f"Google served a /sorry/ interstitial: {url}")
        low = html.lower()
        if any(marker in low for marker in _BROWSER_CAPTCHA_MARKERS):
            raise CaptchaError("Google served a CAPTCHA / unusual-traffic page")

    async def _ensure_browser(self):
        if self._browser is None:
            import nodriver as uc

            kwargs: dict = {"headless": self._headless}
            if self._user_data_dir:
                profile = os.path.expanduser(self._user_data_dir)
                os.makedirs(profile, exist_ok=True)
                kwargs["user_data_dir"] = profile
            self._browser = await uc.start(**kwargs)
            log.info(
                "Launched Chrome via nodriver (headless=%s, profile=%s)",
                self._headless,
                kwargs.get("user_data_dir", "<temp>"),
            )
        return self._browser

    async def _search_async(self, query: str) -> list[str]:
        browser = await self._ensure_browser()
        seen: set[str] = set()
        out: list[str] = []
        for page in range(self._max_pages):
            if page > 0:
                # Realistic pause before pulling the next results page.
                await asyncio.sleep(self._page_delay())
            tab = await browser.get(self._build_url(query, page))
            await asyncio.sleep(self._settle)  # let results render
            html = await tab.get_content()
            try:
                self._detect_block(html, getattr(tab, "url", "") or "")
            except CaptchaError:
                # If a later page is blocked, keep the results we already have
                # (don't discard page 1). Only a blocked first page signals a
                # real rate-limit to the caller.
                if not out:
                    raise
                log.warning("CAPTCHA on page %d — keeping %d earlier results", page + 1, len(out))
                break
            fresh = [u for u in parse_result_urls(html) if u not in seen]
            seen.update(fresh)
            out.extend(fresh)
            if not fresh or not self._has_next_page(html):
                break  # no page 2 (or it added nothing) → done
        return out

    def search(self, query: str) -> list[str]:
        """Run one Google search (following page 2 when present); return result URLs."""
        urls = self._loop.run_until_complete(self._search_async(query))
        log.info("Browser search %r → %d result URLs", query, len(urls))
        return urls

    async def _fetch_html_async(self, url: str) -> str:
        browser = await self._ensure_browser()
        tab = await browser.get(url, new_tab=True)  # open the result in its own tab
        await asyncio.sleep(self._tab_settle)  # minimal pause to let it render
        html = await tab.get_content()
        try:
            await tab.close()
        except Exception:  # closing is best-effort
            log.debug("Failed to close job tab for %s", url, exc_info=True)
        return html

    def fetch_html(self, url: str) -> str | None:
        """Open one result URL in a Chrome tab and return its rendered HTML.

        Going through the browser (same trusted session) means JS-heavy ATS
        pages render, and the time spent here naturally paces the next Google
        search. Returns ``None`` on any failure (caller skips the job).
        """
        try:
            return self._loop.run_until_complete(self._fetch_html_async(url))
        except Exception as exc:
            log.warning("Browser fetch failed for %s: %s", url, exc)
            return None

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
