"""URL normalization and ATS pattern matching for Phase 2 (Module M1-2).

Google results are raw URLs. Before we can dedup or fetch them we:

1. **Normalize** — strip query params and trailing slashes, drop a ``www.``
   prefix, and lowercase only the host (ATS paths can be case-sensitive,
   e.g. Ashby slugs), so two spellings of the same posting collapse to one
   string for the URL-based dedup.
2. **Match** — run the normalized ``host/path`` against per-ATS regexes to
   recover ``(ats_type, company, job_id)`` and build a ``global_id`` in the
   same ``{ats}:{id}`` form jobhive uses. That lets Phase 2 dedup against
   jobs Phase 1 already stored (secondary dedup) and tells the extractor
   which ATS it's dealing with.

Unrecognized URLs return ``None`` from :func:`match_url`; the caller logs
and skips them (new patterns get added here as they're discovered).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# Per-ATS extraction patterns, run against the normalized ``host/path``
# string. ``company`` is optional (iCIMS / WorkAtAStartup carry only an id).
# ``global_id`` mirrors jobhive's ``{ats_type}:{ats_id}`` so the two
# pipelines dedup against each other.
_PATTERNS: dict[str, str] = {
    "greenhouse": r"^boards\.greenhouse\.io/(?P<company>[^/]+)/jobs/(?P<job_id>\d+)",
    "lever": r"^jobs\.lever\.co/(?P<company>[^/]+)/(?P<job_id>[a-fA-F0-9-]+)",
    "ashby": r"^jobs\.ashbyhq\.com/(?P<company>[^/]+)/(?P<job_id>[^/]+)",
    "icims": r"^careers\.icims\.com/jobs/(?P<job_id>\d+)/",
    "workday": r"^(?P<company>[^.]+)\.wd\d+\.myworkdayjobs\.com/.+/(?P<job_id>[^/]+)$",
    "workable": r"^apply\.workable\.com/(?P<company>[^/]+)/j/(?P<job_id>[^/]+)",
    "smartrecruiters": r"^jobs\.smartrecruiters\.com/(?P<company>[^/]+)/(?P<job_id>[^/]+)",
    "wellfound": r"^wellfound\.com/company/(?P<company>[^/]+)/jobs/(?P<job_id>[^/]+)",
    "workatastartup": r"^workatastartup\.com/jobs/(?P<job_id>\d+)",
    "oracle": r"^careers\.oracle\.com/jobs/.+/(?P<job_id>[^/]+)",
    "rippling": r"^ats\.rippling\.com/(?P<company>[^/]+)/jobs/(?P<job_id>[^/]+)",
    "gem": r"^jobs\.gem\.com/(?P<company>[^/]+)/(?P<job_id>[^/]+)",
}

# Compiled once at import. Order is preserved (dict insertion order) so the
# first matching ATS wins — patterns are host-anchored and don't overlap.
_COMPILED: dict[str, re.Pattern[str]] = {
    ats: re.compile(rx) for ats, rx in _PATTERNS.items()
}


@dataclass(frozen=True)
class MatchedUrl:
    """A Google result URL resolved to an ATS posting."""

    ats_type: str
    job_id: str
    global_id: str
    normalized_url: str
    company: str | None = None


def normalize_url(url: str) -> str:
    """Canonicalize an ATS job URL for dedup.

    Strips query string and fragment, drops a trailing slash, removes a
    ``www.`` prefix, and lowercases the host only (the path is left as-is
    because some ATS paths are case-sensitive). The scheme is preserved.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    # Drop query + fragment; keep scheme + host + path.
    return urlunsplit((parts.scheme, host, path, "", ""))


def _host_path(normalized: str) -> str:
    """``scheme://host/path`` → ``host/path`` for pattern matching."""
    parts = urlsplit(normalized)
    return f"{parts.netloc}{parts.path}"


def match_url(url: str) -> MatchedUrl | None:
    """Resolve a URL to a :class:`MatchedUrl`, or ``None`` if no ATS matches.

    Normalizes first, then tests each ATS pattern against ``host/path``.
    """
    normalized = normalize_url(url)
    target = _host_path(normalized)
    for ats, pattern in _COMPILED.items():
        m = pattern.match(target)
        if m is None:
            continue
        groups = m.groupdict()
        job_id = groups["job_id"]
        return MatchedUrl(
            ats_type=ats,
            job_id=job_id,
            global_id=f"{ats}:{job_id}",
            normalized_url=normalized,
            company=groups.get("company"),
        )
    return None
