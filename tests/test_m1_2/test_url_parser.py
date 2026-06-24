"""M1-2 — URL normalization and ATS pattern matching."""

from __future__ import annotations

import pytest

from jobpulse.google_search.url_parser import match_url, normalize_url

# ── normalize_url ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Strip query params.
        (
            "https://boards.greenhouse.io/anthropic/jobs/12345?gh_jid=12345",
            "https://boards.greenhouse.io/anthropic/jobs/12345",
        ),
        (
            "https://jobs.lever.co/palantir/abc-def-123?ref=google",
            "https://jobs.lever.co/palantir/abc-def-123",
        ),
        # Strip trailing slash.
        (
            "https://jobs.lever.co/palantir/abc-def-123/",
            "https://jobs.lever.co/palantir/abc-def-123",
        ),
        # Drop www. prefix.
        (
            "https://www.wellfound.com/company/acme/jobs/99",
            "https://wellfound.com/company/acme/jobs/99",
        ),
        # Lowercase host, preserve path case (Ashby slugs are case-sensitive).
        (
            "https://JOBS.AshbyHQ.com/OpenAI/Some-Slug",
            "https://jobs.ashbyhq.com/OpenAI/Some-Slug",
        ),
        # Fragment is dropped.
        (
            "https://jobs.gem.com/acme/xyz#section",
            "https://jobs.gem.com/acme/xyz",
        ),
        # Whitespace trimmed.
        (
            "  https://jobs.lever.co/x/y  ",
            "https://jobs.lever.co/x/y",
        ),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_is_idempotent():
    once = normalize_url("https://www.jobs.lever.co/x/y/?ref=z")
    assert normalize_url(once) == once


# ── match_url: all 12 ATS patterns ────────────────────────────────────────


@pytest.mark.parametrize(
    "url, ats, company, job_id",
    [
        (
            "https://boards.greenhouse.io/anthropic/jobs/12345",
            "greenhouse", "anthropic", "12345",
        ),
        (
            "https://jobs.lever.co/palantir/abc-def-123",
            "lever", "palantir", "abc-def-123",
        ),
        (
            "https://jobs.ashbyhq.com/OpenAI/Some-Slug",
            "ashby", "OpenAI", "Some-Slug",
        ),
        (
            "https://careers.icims.com/jobs/45678/software-engineer/job",
            "icims", None, "45678",
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIACareers/job/US/Senior-Eng_JR99",
            "workday", "nvidia", "Senior-Eng_JR99",
        ),
        (
            "https://apply.workable.com/acme/j/ABC123",
            "workable", "acme", "ABC123",
        ),
        (
            "https://jobs.smartrecruiters.com/Acme/74400099",
            "smartrecruiters", "Acme", "74400099",
        ),
        (
            "https://wellfound.com/company/acme/jobs/55",
            "wellfound", "acme", "55",
        ),
        (
            "https://www.workatastartup.com/jobs/78901",
            "workatastartup", None, "78901",
        ),
        (
            "https://careers.oracle.com/jobs/region/job/240001ABC",
            "oracle", None, "240001ABC",
        ),
        (
            "https://ats.rippling.com/acme/jobs/uuid-1234",
            "rippling", "acme", "uuid-1234",
        ),
        (
            "https://jobs.gem.com/acme/post-9",
            "gem", "acme", "post-9",
        ),
    ],
)
def test_match_url_all_ats(url, ats, company, job_id):
    m = match_url(url)
    assert m is not None
    assert m.ats_type == ats
    assert m.company == company
    assert m.job_id == job_id
    assert m.global_id == f"{ats}:{job_id}"


def test_global_id_matches_jobhive_form():
    """global_id is `{ats}:{id}` so Phase 2 dedups against Phase 1 rows."""
    m = match_url("https://boards.greenhouse.io/anthropic/jobs/12345?gh_jid=12345")
    assert m.global_id == "greenhouse:12345"


def test_match_url_normalizes_first():
    m = match_url("https://www.jobs.lever.co/palantir/abc-def-123/?ref=google")
    assert m.ats_type == "lever"
    assert m.normalized_url == "https://jobs.lever.co/palantir/abc-def-123"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/jobs/123",
        "https://linkedin.com/jobs/view/456",
        "https://indeed.com/viewjob?jk=abc",
        "https://boards.greenhouse.io/anthropic",  # company, no job id
        "not-a-url",
    ],
)
def test_match_url_unrecognized_returns_none(url):
    assert match_url(url) is None
