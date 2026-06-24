"""HTML page routes (Module 5+): the job feed, job detail, and HTMX actions.

The feed is server-rendered. Filtering uses HTMX to GET ``/partials/jobs``
and swap the results list, with ``hx-push-url`` keeping the active filters
in the address bar so a refresh restores them (FR-03.5). Card action
buttons POST to the routes here, which perform the service call and return
a small HTML response (an empty body removes the card; ``HX-Refresh``
reloads the feed).
"""

from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from jobpulse import pipeline
from jobpulse.config import AppConfig
from jobpulse.deps import get_config, get_db
from jobpulse.google_search import pipeline as google_pipeline
from jobpulse.google_search.query_builder import generate_queries, load_locations
from jobpulse.services import (
    analytics_service,
    applied_service,
    blocklist_service,
    companies_service,
    jobs_service,
)

router = APIRouter()
log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

PAGE_SIZE = 20

# Time-posted presets surfaced in the filter dropdown (label, days).
POSTED_PRESETS = [("Any time", ""), ("Last 24h", "1"), ("Last 2 days", "2"), ("Last 3 days", "3")]
SORT_OPTIONS = [("Posted", "posted"), ("Relevance", "relevance"), ("Salary", "salary")]
# Discovery-channel filter (FR-Phase2): jobs.source values.
SOURCE_OPTIONS = [("All sources", ""), ("jobhive", "jobhive"), ("Google", "google_search")]

# Applied-job pipeline statuses (value, label) for the inline dropdown (FR-04.3).
STATUS_OPTIONS = [
    ("applied", "Applied"),
    ("phone_screen", "Phone Screen"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
    ("ghosted", "Ghosted"),
]


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
        "source": q.get("source", ""),
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
        "source": form["source"] or None,
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
        "source_options": SOURCE_OPTIONS,
        # When a scrape OR a Google search is running, the feed auto-refreshes so
        # new jobs (from either channel) appear live.
        "scrape_running": pipeline.is_running() or google_pipeline.is_running(),
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


# --- Applied tracker -------------------------------------------------------


@router.get("/applied", response_class=HTMLResponse)
def applied_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
):
    result = applied_service.list_applied(
        conn, search=search or None, status=status or None, limit=200
    )
    ctx = {
        "request": request,
        "applied": result["jobs"],
        "total": result["total"],
        "filters": {"search": search or "", "status": status or ""},
        "status_options": STATUS_OPTIONS,
    }
    return templates.TemplateResponse(request, "applied.html", ctx)


@router.post("/applied/{applied_id}/update", response_class=HTMLResponse)
def applied_update(
    applied_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    status: str = Form(...),
    notes: str = Form(""),
    follow_up_date: str = Form(""),
):
    """Inline edit of an applied row (status / notes / follow-up). Returns the
    re-rendered row so HTMX can swap it in place (FR-04.3)."""
    applied_service.update_applied(
        conn,
        applied_id,
        status=status or None,
        notes=notes,
        follow_up_date=follow_up_date or None,
    )
    row = applied_service.get_applied(conn, applied_id)
    return templates.TemplateResponse(
        request,
        "components/applied_card.html",
        {"request": request, "job": row, "status_options": STATUS_OPTIONS, "saved": True},
    )


# --- Blocklist -------------------------------------------------------------


@router.get("/blocklist", response_class=HTMLResponse)
def blocklist_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "blocklist.html",
        {"request": request, "blocked": blocklist_service.list_blocked(conn)},
    )


@router.post("/blocklist/add", response_class=HTMLResponse)
def blocklist_add(
    conn: sqlite3.Connection = Depends(get_db),
    company: str = Form(...),
    reason: str = Form(""),
):
    if company.strip():
        blocklist_service.add_company(conn, company, reason or None)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/blocklist/{block_id}/remove", response_class=HTMLResponse)
def blocklist_remove(block_id: int, conn: sqlite3.Connection = Depends(get_db)):
    blocklist_service.remove_company(conn, block_id)
    return HTMLResponse("")  # empty body → HTMX removes the row


# --- Analytics -------------------------------------------------------------

# Date-range presets for the analytics dashboard (label, days; "" = all time).
RANGE_PRESETS = [("Last 7 days", "7"), ("Last 30 days", "30"), ("Last 90 days", "90"), ("All time", "")]


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
    days: int | None = Query(None, ge=1),
):
    data = analytics_service.summary(conn, config.target_roles, days=days)
    ctx = {
        "request": request,
        "data": data,
        # Embedded for Chart.js (escape </ to keep it safe inside <script>).
        "data_json": json.dumps(data).replace("</", "<\\/"),
        "selected_days": str(days) if days else "",
        "range_presets": RANGE_PRESETS,
    }
    return templates.TemplateResponse(request, "analytics.html", ctx)


# --- Companies (in-region yield) -------------------------------------------

# Filter tabs for the Companies page (view key, label).
COMPANY_VIEWS = [
    ("foreign", "Not hiring in-region"),
    ("zero", "Returned jobs, none in-region"),
    ("productive", "Hiring in-region"),
    ("all", "All tracked"),
]


@router.get("/companies", response_class=HTMLResponse)
def companies_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
    view: str = Query("foreign"),
):
    if view not in dict(COMPANY_VIEWS):
        view = "foreign"
    threshold = config.scrape.skip_after_runs
    ctx = {
        "request": request,
        "view": view,
        "views": COMPANY_VIEWS,
        "counts": companies_service.counts(conn, skip_after_runs=threshold),
        "companies": companies_service.list_companies(conn, view=view, skip_after_runs=threshold),
        "row_limit": companies_service.ROW_LIMIT,
        "skip_after_runs": threshold,
        "recheck_days": config.scrape.recheck_days,
    }
    return templates.TemplateResponse(request, "companies.html", ctx)


# --- Scrape logs -----------------------------------------------------------


def _scrape_logs_context(request: Request, conn: sqlite3.Connection, config: AppConfig) -> dict:
    rows = conn.execute(
        "SELECT * FROM scrape_runs ORDER BY run_at DESC, id DESC LIMIT 100"
    ).fetchall()
    runs = [dict(r) for r in rows]

    # Per-ATS breakdown grouped by run id (only for the runs we're showing).
    breakdown: dict[int, list[dict]] = {}
    if runs:
        run_ids = [r["id"] for r in runs]
        placeholders = ",".join("?" for _ in run_ids)
        ats_rows = conn.execute(
            f"SELECT * FROM scrape_run_ats WHERE run_id IN ({placeholders}) "
            f"ORDER BY jobs_fetched DESC",
            run_ids,
        ).fetchall()
        for ar in ats_rows:
            breakdown.setdefault(ar["run_id"], []).append(dict(ar))

    # Phase 2 Google-search runs (separate channel, own audit table).
    search_runs = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM search_runs ORDER BY run_at DESC, id DESC LIMIT 50"
        ).fetchall()
    ]

    return {
        "request": request,
        "runs": runs,
        "breakdown": breakdown,
        "search_runs": search_runs,
        "cron_enabled": config.cron.enabled,
        "pipeline_status": pipeline.get_status(),
        "search_status": google_pipeline.get_status(),
    }


@router.get("/scrape-logs", response_class=HTMLResponse)
def scrape_logs_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    return templates.TemplateResponse(request, "scrape_logs.html", _scrape_logs_context(request, conn, config))


@router.get("/partials/scrape-logs", response_class=HTMLResponse)
def scrape_logs_partial(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Live region of the scrape-logs page — polled every 2s while running."""
    return templates.TemplateResponse(
        request, "components/scrape_live.html", _scrape_logs_context(request, conn, config)
    )


@router.post("/scrape/run", response_class=HTMLResponse)
def scrape_run_action(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Dev-mode manual trigger: run a scrape in the background (FR-01 manual).

    Returns immediately with the live region (now showing "running" + a 2s
    poller); a run-lock prevents overlap.
    """
    pipeline.run_scrape_in_background(config, schedule_slot="manual")
    return templates.TemplateResponse(
        request, "components/scrape_live.html", _scrape_logs_context(request, conn, config)
    )


@router.post("/cleanup/run", response_class=HTMLResponse)
def cleanup_run_action(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Manual trigger: run TTL cleanup in the background."""
    pipeline.run_cleanup_in_background(config)
    return templates.TemplateResponse(
        request, "components/scrape_live.html", _scrape_logs_context(request, conn, config)
    )


@router.post("/internet-search/run", response_class=HTMLResponse)
def internet_search_run_action(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Search the internet (Google) for the full configured matrix — no input.

    Builds queries from ``config.target_roles`` × ``config.ats_platforms`` ×
    locations (past 24h via ``tbs=qdr:d``), shuffles them so a capped run
    samples broadly, and fires a polite background batch that self-stops at
    ``google_search.max_queries_per_run``. Results land in the same feed as
    Phase 1 (source='google_search').
    """
    queries, skipped = generate_queries(
        config, load_locations(), shuffle=True, rng=random.Random()
    )
    if skipped:
        log.warning("Internet search: skipping config ATS with no Phase 2 support: %s", skipped)
    if queries:
        google_pipeline.run_google_search_in_background(
            config, queries=queries, schedule_slot="manual"
        )
    return templates.TemplateResponse(
        request, "components/scrape_live.html", _scrape_logs_context(request, conn, config)
    )
