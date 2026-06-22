"""HTML page routes (Module 5+): the job feed, job detail, and HTMX actions.

The feed is server-rendered. Filtering uses HTMX to GET ``/partials/jobs``
and swap the results list, with ``hx-push-url`` keeping the active filters
in the address bar so a refresh restores them (FR-03.5). Card action
buttons POST to the routes here, which perform the service call and return
a small HTML response (an empty body removes the card; ``HX-Refresh``
reloads the feed).
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from jobpulse.config import AppConfig
from jobpulse.deps import get_config, get_db
from jobpulse.services import applied_service, blocklist_service, jobs_service

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

PAGE_SIZE = 20

# Time-posted presets surfaced in the filter dropdown (label, days).
POSTED_PRESETS = [("Any time", ""), ("Last 24h", "1"), ("Last 2 days", "2"), ("Last 3 days", "3")]
SORT_OPTIONS = [("Posted", "posted"), ("Relevance", "relevance"), ("Salary", "salary")]


def _parse_filters(request: Request) -> tuple[dict, dict]:
    """Split query params into (service kwargs, raw form values for repopulation)."""
    q = request.query_params
    form = {
        "search": q.get("search", ""),
        "role": q.get("role", ""),
        "ats": q.get("ats", ""),
        "location": q.get("location", ""),
        "remote": q.get("remote", ""),
        "employment_type": q.get("employment_type", ""),
        "posted_within_days": q.get("posted_within_days", ""),
        "salary_min": q.get("salary_min", ""),
        "sort": q.get("sort", ""),
    }
    try:
        offset = max(int(q.get("offset", "0")), 0)
    except ValueError:
        offset = 0

    kwargs = {
        "search": form["search"] or None,
        "role": form["role"] or None,
        "ats": form["ats"] or None,
        "location": form["location"] or None,
        "remote_only": form["remote"] in ("true", "on", "1"),
        "employment_type": form["employment_type"] or None,
        "posted_within_days": int(form["posted_within_days"]) if form["posted_within_days"] else None,
        "salary_min": float(form["salary_min"]) if form["salary_min"] else None,
        "sort": form["sort"] or None,
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    return kwargs, form


def _base_query_string(form: dict) -> str:
    """Querystring of active filters (no offset) for building pagination links."""
    pairs = {k: v for k, v in form.items() if v}
    return urlencode(pairs)


def _feed_context(request: Request, conn: sqlite3.Connection, config: AppConfig) -> dict:
    kwargs, form = _parse_filters(request)
    try:
        result = jobs_service.list_jobs(conn, **kwargs)
    except ValueError:
        # Invalid sort etc. — fall back to defaults rather than 500.
        kwargs["sort"] = None
        form["sort"] = ""
        result = jobs_service.list_jobs(conn, **kwargs)

    offset = kwargs["offset"]
    total = result["total"]
    return {
        "request": request,
        "jobs": result["jobs"],
        "total": total,
        "offset": offset,
        "limit": PAGE_SIZE,
        "page": offset // PAGE_SIZE + 1,
        "pages": max(math.ceil(total / PAGE_SIZE), 1),
        "has_prev": offset > 0,
        "has_next": offset + PAGE_SIZE < total,
        "prev_offset": max(offset - PAGE_SIZE, 0),
        "next_offset": offset + PAGE_SIZE,
        "base_qs": _base_query_string(form),
        "filters": form,
        "ats_options": config.ats_platforms.all_platforms,
        "posted_presets": POSTED_PRESETS,
        "sort_options": SORT_OPTIONS,
    }


@router.get("/", response_class=HTMLResponse)
def feed(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    ctx = _feed_context(request, conn, config)
    return templates.TemplateResponse(request, "feed.html", ctx)


@router.get("/partials/jobs", response_class=HTMLResponse)
def jobs_partial(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    ctx = _feed_context(request, conn, config)
    return templates.TemplateResponse(request, "components/job_list.html", ctx)


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    job = jobs_service.get_job(conn, job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "job_detail.html", {"request": request, "job": None}, status_code=404
        )
    # Opening the detail view clears the "New" badge (FR-02.5).
    jobs_service.mark_viewed(conn, job_id)
    return templates.TemplateResponse(request, "job_detail.html", {"request": request, "job": job})


# --- HTMX card actions -----------------------------------------------------


@router.post("/job/{job_id}/expire", response_class=HTMLResponse)
def expire_action(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    jobs_service.expire_job(conn, job_id)
    return HTMLResponse("")  # empty body + outerHTML swap removes the card


@router.post("/job/{job_id}/apply", response_class=HTMLResponse)
def apply_action(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    applied_service.mark_applied(conn, job_id)
    return HTMLResponse("")


@router.post("/job/{job_id}/block", response_class=HTMLResponse)
def block_action(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    job = jobs_service.get_job(conn, job_id)
    if job is not None:
        blocklist_service.add_company(conn, job["company"])
    # Reload the feed so every job from that company disappears at once.
    return Response(status_code=204, headers={"HX-Refresh": "true"})
