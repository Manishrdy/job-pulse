from __future__ import annotations

import uuid
from datetime import datetime, timezone

from jobhive.models import ATSType

from jobpulse.models import INSERT_COLUMNS, JobRecord
from tests.conftest import make_jobhive_job


def test_all_fields_mapped():
    job = make_jobhive_job(
        url="https://boards.greenhouse.io/acme/jobs/777",
        apply_url="https://apply.greenhouse.io/acme/777",
        title="Senior Backend Engineer",
        company="acme",
        ats_type=ATSType.GREENHOUSE,
        ats_id="777",
        location="San Francisco, CA",
        country_iso="US",
        is_remote=True,
        salary_min=150000.0,
        salary_max=200000.0,
        salary_currency="USD",
        salary_period="YEAR",
        salary_summary="$150K - $200K",
        employment_type="FULL_TIME",
        department="Engineering",
        team="Platform",
        experience=5,
        description="Build things.",
        posted_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        language="en",
        requisition_id="REQ-1",
    )
    rec = JobRecord.from_jobhive(job, company_name="Acme Corp")

    assert rec.global_id == "greenhouse:777"
    assert rec.url == "https://boards.greenhouse.io/acme/jobs/777"
    assert rec.apply_url == "https://apply.greenhouse.io/acme/777"
    assert rec.title == "Senior Backend Engineer"
    assert rec.company == "Acme Corp"  # display name override
    assert rec.ats_type == "greenhouse"  # enum -> value
    assert rec.ats_id == "777"
    assert rec.location == "San Francisco, CA"
    assert rec.country_iso == "US"
    assert rec.is_remote == 1  # bool -> int
    assert rec.salary_min == 150000.0
    assert rec.salary_max == 200000.0
    assert rec.salary_currency == "USD"
    assert rec.salary_period == "YEAR"
    assert rec.employment_type == "FULL_TIME"
    assert rec.department == "Engineering"
    assert rec.team == "Platform"
    assert rec.experience == 5
    assert rec.description == "Build things."
    assert rec.posted_at == "2026-01-15T12:00:00+00:00"  # datetime -> ISO str
    assert rec.language == "en"
    assert rec.requisition_id == "REQ-1"
    assert rec.is_blocked == 0


def test_company_defaults_to_job_company_when_no_override():
    job = make_jobhive_job(company="acme")
    rec = JobRecord.from_jobhive(job)
    assert rec.company == "acme"


def test_company_name_empty_string_falls_back():
    job = make_jobhive_job(company="acme")
    rec = JobRecord.from_jobhive(job, company_name="")
    assert rec.company == "acme"


def test_is_remote_false_maps_to_zero():
    job = make_jobhive_job(is_remote=False)
    rec = JobRecord.from_jobhive(job)
    assert rec.is_remote == 0


def test_is_remote_inferred_from_title_when_null():
    job = make_jobhive_job(title="Remote Software Engineer", is_remote=None)
    rec = JobRecord.from_jobhive(job)
    assert rec.is_remote == 1


def test_is_remote_stays_null_when_no_signal():
    job = make_jobhive_job(title="Software Engineer", is_remote=None)
    rec = JobRecord.from_jobhive(job)
    assert rec.is_remote is None


def test_salary_parsed_from_summary_when_unstructured():
    job = make_jobhive_job(
        salary_summary="$120K - $160K",
        salary_min=None,
        salary_max=None,
    )
    rec = JobRecord.from_jobhive(job)
    assert rec.salary_min == 120000.0
    assert rec.salary_max == 160000.0


def test_structured_salary_not_overwritten_by_parse():
    job = make_jobhive_job(
        salary_summary="ignore me $1 - $2",
        salary_min=100000.0,
        salary_max=120000.0,
    )
    rec = JobRecord.from_jobhive(job)
    assert rec.salary_min == 100000.0
    assert rec.salary_max == 120000.0


def test_null_description_posted_at_salary_handled():
    job = make_jobhive_job(
        description=None,
        posted_at=None,
        salary_min=None,
        salary_max=None,
        salary_summary=None,
    )
    rec = JobRecord.from_jobhive(job)
    assert rec.description is None
    assert rec.posted_at is None
    assert rec.salary_min is None
    assert rec.salary_max is None
    assert rec.salary_summary is None


def test_malformed_ats_id_uuid_fallback():
    job = make_jobhive_job(ats_id=None)
    rec = JobRecord.from_jobhive(job)
    # global_id should NOT be "greenhouse:..." — it falls back to a UUID4.
    assert not rec.global_id.startswith("greenhouse:")
    # And it should parse as a valid UUID.
    uuid.UUID(rec.global_id)


def test_empty_ats_id_uuid_fallback():
    job = make_jobhive_job(ats_id="")
    rec = JobRecord.from_jobhive(job)
    assert not rec.global_id.startswith("greenhouse:")
    uuid.UUID(rec.global_id)


def test_insert_values_aligns_with_columns():
    rec = JobRecord.from_jobhive(make_jobhive_job())
    values = rec.insert_values()
    assert len(values) == len(INSERT_COLUMNS)
    # Spot-check a couple of positions.
    assert values[INSERT_COLUMNS.index("global_id")] == rec.global_id
    assert values[INSERT_COLUMNS.index("is_blocked")] == rec.is_blocked


def test_is_blocked_flag_passthrough():
    rec = JobRecord.from_jobhive(make_jobhive_job(), is_blocked=True)
    assert rec.is_blocked == 1
