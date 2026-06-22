"""REST API endpoints (FR-02–FR-07).

Thin JSON layer over the service functions. Templates (Module 5/6) call
these via HTMX/fetch. Each handler resolves a per-request DB connection
via the ``get_db`` dependency.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from jobpulse.config import AppConfig
from jobpulse.deps import get_config, get_db
from jobpulse.services import (
    analytics_service,
    applied_service,
    blocklist_service,
    jobs_service,
)

router = APIRouter(prefix="/api")


# --- Request bodies --------------------------------------------------------


class BlocklistAdd(BaseModel):
    company: str
    reason: str | None = None


class AppliedUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    follow_up_date: str | None = None


# --- Jobs ------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(
    conn: sqlite3.Connection = Depends(get_db),
    search: str | None = None,
    role: str | None = None,
    ats: str | None = None,
    location: str | None = None,
    remote: bool = False,
    employment_type: str | None = None,
    posted_within_days: int | None = None,
    salary_min: float | None = None,
    sort: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    try:
        return jobs_service.list_jobs(
            conn,
            search=search,
            role=role,
            ats=ats,
            location=location,
            remote_only=remote,
            employment_type=employment_type,
            posted_within_days=posted_within_days,
            salary_min=salary_min,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job(job_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    job = jobs_service.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/expire")
def expire_job(job_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"expired": jobs_service.expire_job(conn, job_id)}


@router.post("/jobs/{job_id}/viewed")
def mark_viewed(job_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"viewed": jobs_service.mark_viewed(conn, job_id)}


@router.post("/jobs/{job_id}/apply")
def apply_job(job_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    applied_id = applied_service.mark_applied(conn, job_id)
    if applied_id is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"applied_id": applied_id}


# --- Applied ---------------------------------------------------------------


@router.get("/applied")
def list_applied(
    conn: sqlite3.Connection = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    return applied_service.list_applied(
        conn, search=search, status=status, limit=limit, offset=offset
    )


@router.patch("/applied/{applied_id}")
def update_applied(
    applied_id: int,
    body: AppliedUpdate,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        updated = applied_service.update_applied(
            conn,
            applied_id,
            status=body.status,
            notes=body.notes,
            follow_up_date=body.follow_up_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Applied job not found or no changes")
    return {"updated": True}


# --- Blocklist -------------------------------------------------------------


@router.get("/blocklist")
def list_blocklist(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return blocklist_service.list_blocked(conn)


@router.post("/blocklist")
def add_blocklist(body: BlocklistAdd, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        block_id = blocklist_service.add_company(conn, body.company, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": block_id}


@router.delete("/blocklist/{block_id}")
def remove_blocklist(block_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    if not blocklist_service.remove_company(conn, block_id):
        raise HTTPException(status_code=404, detail="Blocklist entry not found")
    return {"removed": True}


# --- Analytics & audit -----------------------------------------------------


@router.get("/analytics/summary")
def analytics_summary(
    conn: sqlite3.Connection = Depends(get_db),
    config: AppConfig = Depends(get_config),
    days: int | None = Query(None, ge=1),
) -> dict:
    return analytics_service.summary(conn, config.target_roles, days=days)


@router.get("/scrape-runs")
def scrape_runs(
    conn: sqlite3.Connection = Depends(get_db),
    limit: int = Query(20, ge=1, le=200),
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM scrape_runs ORDER BY run_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
