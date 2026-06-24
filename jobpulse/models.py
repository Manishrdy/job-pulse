"""Internal Job representation for JobPulse.

Maps the canonical jobhive ``Job`` model onto the subset of fields our
``jobs`` table stores (see SCOPE.md §5.2). The jobhive model carries
fields we don't persist (``region``, ``lat``/``lon``, ``commitment``,
``fetched_at``, ``raw``); those are dropped here. DB-managed columns
(``id``, ``first_seen``, ``last_seen``, ``viewed_at``, ``status``,
``expired_at``) are not part of this record — they're set by the
database layer at ingest time.
"""

from __future__ import annotations

from datetime import datetime

from jobhive.enrichment import infer_is_remote, parse_salary_range
from jobhive.models import Job as JobhiveJob
from pydantic import BaseModel, ConfigDict

# Columns of ``jobs`` carried by a JobRecord, in insert order. DB-managed
# columns (id/first_seen/last_seen/viewed_at/status/expired_at) are excluded.
INSERT_COLUMNS: tuple[str, ...] = (
    "global_id",
    "url",
    "apply_url",
    "title",
    "company",
    "ats_type",
    "ats_id",
    "location",
    "country_iso",
    "is_remote",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_summary",
    "employment_type",
    "department",
    "team",
    "experience",
    "description",
    "posted_at",
    "language",
    "requisition_id",
    "is_blocked",
    "source",
)


def _url_to_str(value: object) -> str | None:
    """Pydantic HttpUrl (or str/None) → plain string for SQLite storage."""
    if value is None:
        return None
    return str(value)


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class JobRecord(BaseModel):
    """A job ready to be written to the ``jobs`` table.

    Field types are SQLite-friendly (str/int/float/None) — URLs are
    stringified, ``is_remote`` is 0/1/None, timestamps are ISO strings.
    """

    model_config = ConfigDict(frozen=True)

    global_id: str
    url: str
    apply_url: str | None = None
    title: str
    company: str
    ats_type: str
    ats_id: str | None = None
    location: str | None = None
    country_iso: str | None = None
    is_remote: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_summary: str | None = None
    employment_type: str | None = None
    department: str | None = None
    team: str | None = None
    experience: int | None = None
    description: str | None = None
    posted_at: str | None = None
    language: str | None = None
    requisition_id: str | None = None
    is_blocked: int = 0
    # Discovery channel: 'jobhive' (Phase 1 scrape) or 'google_search' (Phase 2).
    source: str = "jobhive"

    @classmethod
    def from_jobhive(
        cls,
        job: JobhiveJob,
        *,
        company_name: str | None = None,
        is_blocked: bool = False,
    ) -> JobRecord:
        """Convert a jobhive ``Job`` into a JobRecord.

        ``company_name`` overrides ``job.company`` — some scrapers
        (Greenhouse) set ``company`` to the ATS slug rather than the
        display name; the caller passes the manifest's display name here.

        Two cheap enrichment passes fill gaps the scraper left null:
        - ``is_remote`` is inferred from the title when the scraper
          didn't surface a flag (only ever asserts True, never False).
        - ``salary_min``/``salary_max`` are parsed from
          ``salary_summary`` when present but not structured.
        """
        is_remote = job.is_remote
        if is_remote is None:
            is_remote = infer_is_remote(job.title)

        salary_min = job.salary_min
        salary_max = job.salary_max
        if salary_min is None and salary_max is None and job.salary_summary:
            parsed_min, parsed_max = parse_salary_range(job.salary_summary)
            salary_min, salary_max = parsed_min, parsed_max

        return cls(
            global_id=job.global_id,
            url=_url_to_str(job.url),  # type: ignore[arg-type]
            apply_url=_url_to_str(job.apply_url),
            title=job.title,
            company=company_name if company_name else job.company,
            ats_type=job.ats_type.value,
            ats_id=job.ats_id,
            location=job.location,
            country_iso=job.country_iso,
            is_remote=_bool_to_int(is_remote),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            salary_summary=job.salary_summary,
            employment_type=job.employment_type,
            department=job.department,
            team=job.team,
            experience=job.experience,
            description=job.description,
            posted_at=_dt_to_iso(job.posted_at),
            language=job.language,
            requisition_id=job.requisition_id,
            is_blocked=1 if is_blocked else 0,
        )

    def insert_values(self) -> tuple[object, ...]:
        """Values tuple aligned with ``INSERT_COLUMNS`` for parameterized insert."""
        return tuple(getattr(self, col) for col in INSERT_COLUMNS)
