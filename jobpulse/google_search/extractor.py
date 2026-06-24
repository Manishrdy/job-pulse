"""Fetch a matched URL and build a JobRecord (Module M1-6).

We chose *direct HTTP fetch of the single job page* (not a full company
re-scrape). For the highest-volume ATS we hit their clean per-job JSON
endpoints; everything else falls back to parsing the schema.org
``JobPosting`` JSON-LD block that most ATS embed, then a last-ditch
``<title>``.

The HTTP call is injected (``fetch``) so tests run offline. Any failure
(non-200, parse error, missing title) yields ``None`` — the caller logs and
skips, never crashing the run.

The produced :class:`~jobpulse.models.JobRecord` carries
``source='google_search'`` and stores the *normalized* URL so the URL-based
dedup matches on the next pass.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Callable

import httpx
from jobhive.enrichment import infer_is_remote

from jobpulse.google_search.url_parser import MatchedUrl
from jobpulse.models import JobRecord

log = logging.getLogger(__name__)

Fetch = Callable[[str], httpx.Response]


def _bool_to_int(v: bool | None) -> int | None:
    return None if v is None else (1 if v else 0)


def _build_record(
    match: MatchedUrl,
    *,
    title: str | None,
    company: str | None,
    location: str | None = None,
    description: str | None = None,
    salary_summary: str | None = None,
    employment_type: str | None = None,
    posted_at: str | None = None,
) -> JobRecord | None:
    """Assemble a JobRecord, or None if there's no usable title."""
    title = (title or "").strip()
    if not title:
        log.warning("No title extracted for %s — skipping", match.normalized_url)
        return None
    return JobRecord(
        global_id=match.global_id,
        url=match.normalized_url,
        title=title,
        company=(company or match.company or match.ats_type).strip(),
        ats_type=match.ats_type,
        ats_id=match.job_id,
        location=location,
        is_remote=_bool_to_int(infer_is_remote(title)),
        salary_summary=salary_summary,
        employment_type=employment_type,
        description=description,
        posted_at=posted_at,
        source="google_search",
    )


# ── Per-ATS JSON endpoints (primary, high-volume ATS) ─────────────────────


def _extract_greenhouse(match: MatchedUrl, fetch: Fetch) -> JobRecord | None:
    api = f"https://boards-api.greenhouse.io/v1/boards/{match.company}/jobs/{match.job_id}"
    resp = fetch(api)
    if resp.status_code != 200:
        return None
    data = resp.json()
    location = (data.get("location") or {}).get("name")
    return _build_record(
        match,
        title=data.get("title"),
        company=data.get("company_name") or match.company,
        location=location,
        description=html.unescape(data["content"]) if data.get("content") else None,
        posted_at=data.get("updated_at"),
    )


def _extract_lever(match: MatchedUrl, fetch: Fetch) -> JobRecord | None:
    api = f"https://api.lever.co/v0/postings/{match.company}/{match.job_id}"
    resp = fetch(api)
    if resp.status_code != 200:
        return None
    data = resp.json()
    categories = data.get("categories") or {}
    return _build_record(
        match,
        title=data.get("text"),
        company=match.company,
        location=categories.get("location"),
        description=data.get("descriptionPlain") or data.get("description"),
        employment_type=categories.get("commitment"),
    )


# ── Generic HTML / JSON-LD fallback (everything else) ─────────────────────


def _find_job_posting_ld(html_text: str) -> dict | None:
    """Return the schema.org JobPosting JSON-LD object if present."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        # JSON-LD may be a single object, a list, or an @graph wrapper.
        candidates = parsed if isinstance(parsed, list) else [parsed]
        if isinstance(parsed, dict) and "@graph" in parsed:
            candidates = parsed["@graph"]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                return obj
    return None


def _ld_location(posting: dict) -> str | None:
    loc = posting.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if not isinstance(addr, dict):
        return None
    parts = [
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None,
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _extract_html(match: MatchedUrl, fetch: Fetch) -> JobRecord | None:
    from bs4 import BeautifulSoup

    resp = fetch(match.normalized_url)
    if resp.status_code != 200:
        return None
    posting = _find_job_posting_ld(resp.text)
    if posting is not None:
        org = posting.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else None
        desc = posting.get("description")
        return _build_record(
            match,
            title=posting.get("title"),
            company=company,
            location=_ld_location(posting),
            description=html.unescape(desc) if isinstance(desc, str) else None,
            employment_type=posting.get("employmentType"),
            posted_at=posting.get("datePosted"),
        )
    # Last resort: the page <title>.
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    return _build_record(match, title=title, company=match.company)


# ATS with a dedicated JSON extractor; the rest use the HTML fallback.
_JSON_EXTRACTORS: dict[str, Callable[[MatchedUrl, Fetch], JobRecord | None]] = {
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
}


def extract(match: MatchedUrl, *, fetch: Fetch) -> JobRecord | None:
    """Fetch and parse a matched URL into a JobRecord, or None on any failure."""
    extractor = _JSON_EXTRACTORS.get(match.ats_type, _extract_html)
    try:
        return extractor(match, fetch)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        log.warning("Extraction failed for %s: %s", match.normalized_url, exc)
        return None
