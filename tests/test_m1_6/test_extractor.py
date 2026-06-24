"""M1-6 — job extractor: JSON endpoints + JSON-LD/HTML fallback."""

from __future__ import annotations

import json

import httpx

from jobpulse.google_search.extractor import extract
from jobpulse.google_search.url_parser import match_url


def _resp(status=200, *, json_body=None, text=""):
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text)


def _fetch_returning(resp):
    return lambda _url: resp


# ── Greenhouse JSON ───────────────────────────────────────────────────────


def test_greenhouse_json_extraction():
    match = match_url("https://boards.greenhouse.io/anthropic/jobs/12345")
    body = {
        "title": "Software Engineer",
        "company_name": "Anthropic",
        "location": {"name": "San Francisco, CA"},
        "content": "&lt;p&gt;Build things&lt;/p&gt;",
        "updated_at": "2026-06-23T10:00:00-04:00",
    }
    rec = extract(match, fetch=_fetch_returning(_resp(json_body=body)))
    assert rec is not None
    assert rec.title == "Software Engineer"
    assert rec.company == "Anthropic"
    assert rec.location == "San Francisco, CA"
    assert rec.description == "<p>Build things</p>"  # unescaped
    assert rec.global_id == "greenhouse:12345"
    assert rec.ats_type == "greenhouse"
    assert rec.ats_id == "12345"
    assert rec.url == "https://boards.greenhouse.io/anthropic/jobs/12345"
    assert rec.source == "google_search"


def test_greenhouse_non_200_returns_none():
    match = match_url("https://boards.greenhouse.io/anthropic/jobs/12345")
    assert extract(match, fetch=_fetch_returning(_resp(404))) is None


# ── Lever JSON ────────────────────────────────────────────────────────────


def test_lever_json_extraction():
    match = match_url("https://jobs.lever.co/palantir/abc-def-123")
    body = {
        "text": "Backend Engineer",
        "categories": {"location": "New York", "commitment": "Full-time"},
        "descriptionPlain": "Do backend work",
    }
    rec = extract(match, fetch=_fetch_returning(_resp(json_body=body)))
    assert rec.title == "Backend Engineer"
    assert rec.location == "New York"
    assert rec.employment_type == "Full-time"
    assert rec.description == "Do backend work"
    assert rec.company == "palantir"
    assert rec.source == "google_search"


# ── HTML / JSON-LD fallback ───────────────────────────────────────────────


def test_html_json_ld_extraction():
    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    ld = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "AI Engineer",
        "description": "<p>Train models</p>",
        "datePosted": "2026-06-22",
        "employmentType": "FULL_TIME",
        "hiringOrganization": {"@type": "Organization", "name": "Acme AI"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Austin",
                "addressRegion": "TX",
            },
        },
    }
    page = f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'
    rec = extract(match, fetch=_fetch_returning(_resp(text=page)))
    assert rec.title == "AI Engineer"
    assert rec.company == "Acme AI"
    assert rec.location == "Austin, TX"
    assert rec.employment_type == "FULL_TIME"
    assert rec.ats_type == "icims"
    assert rec.source == "google_search"


def test_html_graph_wrapped_json_ld():
    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "careers"},
            {"@type": "JobPosting", "title": "Founding Engineer"},
        ],
    }
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    rec = extract(match, fetch=_fetch_returning(_resp(text=page)))
    assert rec.title == "Founding Engineer"


def test_html_title_fallback_when_no_json_ld():
    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    page = "<html><head><title>Senior Engineer - Careers</title></head><body></body></html>"
    rec = extract(match, fetch=_fetch_returning(_resp(text=page)))
    assert rec.title == "Senior Engineer - Careers"
    assert rec.company == "icims"  # no company available → ats_type fallback


def test_html_no_title_returns_none():
    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    rec = extract(match, fetch=_fetch_returning(_resp(text="<html><body></body></html>")))
    assert rec is None


def test_malformed_json_ld_falls_back_to_title():
    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    page = (
        '<script type="application/ld+json">{ not valid json }</script>'
        "<title>Fallback Title</title>"
    )
    rec = extract(match, fetch=_fetch_returning(_resp(text=page)))
    assert rec.title == "Fallback Title"


def test_extract_from_html_directly():
    """The browser engine path: build a record straight from page HTML."""
    from jobpulse.google_search.extractor import extract_from_html

    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    ld = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Staff Engineer",
        "description": "<p>Lead</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Seattle"}},
    }
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    rec = extract_from_html(match, page)
    assert rec.title == "Staff Engineer"
    assert rec.company == "Acme"
    assert rec.location == "Seattle"
    assert rec.source == "google_search"


def test_extract_from_html_title_fallback():
    from jobpulse.google_search.extractor import extract_from_html

    match = match_url("https://careers.icims.com/jobs/45678/swe/job")
    rec = extract_from_html(match, "<html><head><title>SRE - Careers</title></head></html>")
    assert rec.title == "SRE - Careers"


def test_fetch_error_returns_none():
    match = match_url("https://boards.greenhouse.io/anthropic/jobs/12345")

    def boom(_url):
        raise httpx.ConnectError("network down")

    assert extract(match, fetch=boom) is None
