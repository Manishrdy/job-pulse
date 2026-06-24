#!/usr/bin/env python
"""Warm up the persistent Chrome profile used by the Google-search browser engine.

The automated "Search Internet" run tears Chrome down after a few failures, so
it never gives you a chance to solve Google's CAPTCHA / sign in — which is why a
cold profile keeps getting challenged on the very first query.

Run this ONCE (and again whenever Google starts challenging you):

    uv run python scripts/warm_profile.py

It opens Chrome with the *same* profile dir the engine uses
(``google_search.user_data_dir``) on a Google search page, then waits. In that
window:
  1. Solve any CAPTCHA / "unusual traffic" / consent page.
  2. Ideally sign into your Google account (logged-in sessions are rarely
     challenged).
  3. When a normal results page shows, come back here and press Enter.

The trust cookies are saved into the profile, so later automated runs reuse them.
"""

from __future__ import annotations

import os

import nodriver as uc

from jobpulse.config import load_config

# A representative query so you land on the same kind of page the engine hits.
_WARM_URL = (
    "https://www.google.com/search?"
    'q=site:jobs.ashbyhq.com+"AI Engineer"+"United States"&hl=en&tbs=qdr:d'
)


async def _main() -> None:
    gs = load_config().google_search
    if not gs.user_data_dir:
        print("google_search.user_data_dir is empty — set a path in config.yaml first.")
        return
    profile = os.path.expanduser(gs.user_data_dir)
    os.makedirs(profile, exist_ok=True)

    print(f"Opening Chrome with profile: {profile}")
    browser = await uc.start(headless=False, user_data_dir=profile)
    await browser.get(_WARM_URL)
    print(
        "\nChrome is open.\n"
        "  1. Solve any CAPTCHA / 'unusual traffic' / consent page.\n"
        "  2. Sign into your Google account if you can.\n"
        "  3. When a normal results page shows, press Enter here to finish.\n"
    )
    try:
        input("Press Enter when done… ")
    finally:
        browser.stop()
    print("Profile saved. Automated 'Search Internet' runs will reuse it.")


if __name__ == "__main__":
    uc.loop().run_until_complete(_main())
