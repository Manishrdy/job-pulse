# JobPulse

A self-hosted, minimalistic **job search dashboard** built on top of the
[jobhive / ats-scrapers](https://github.com/kalil0321/ats-scrapers) open-source ATS
scraper library. JobPulse vendors the jobhive scrapers locally, runs them on a personal
schedule, stores results in SQLite, and serves a FastAPI dashboard for browsing,
filtering, tracking, and analyzing job applications.

> **Status:** Modules 1–4 complete (config, database, scraper/ingestion, lifecycle,
> and the full service + REST API layer). The HTML dashboard (Jinja2 + HTMX),
> analytics charts, cron scheduling, and deployment docs land in Modules 5–8.
> See [`SCOPE.md`](SCOPE.md) for the full development plan.

---

## Why

- Eliminate manual browsing of career pages, job boards, and LinkedIn.
- Surface fresh jobs 3×/day before aggregators pick them up.
- Track applications, blocklist companies, and keep personal analytics.
- Stay minimal — a single Python process, SQLite, and cron. No containers.

## Tech stack

| Concern | Choice |
|---------|--------|
| Language | Python 3.12 |
| Web | FastAPI + Uvicorn |
| Templates | Jinja2 + HTMX (Module 5+) |
| Database | SQLite (WAL mode) with FTS5 full-text search |
| Scrapers | Vendored [jobhive](https://github.com/kalil0321/ats-scrapers) (`vendor/jobhive/`) |
| Packaging | [uv](https://github.com/astral-sh/uv) |
| Tests | pytest |

The jobhive scrapers are **vendored, not pip-installed** — we own the scraper code and
control the schedule independent of upstream PyPI releases.

---

## Project layout

```
jobpulse/
├── config.py          # Pydantic-validated YAML config loader
├── database.py        # SQLite connection, WAL, schema, FTS5 + triggers
├── logger.py          # Rotating file logger (10MB × 5)
├── models.py          # JobRecord — maps jobhive Job → our schema
├── scoring.py         # FTS5 bm25 relevance scoring
├── scraper.py         # jobhive wrapper, ATS priority order, role filter
├── ingest.py          # Dedup, insert/update, blocklist, scrape_runs logging
├── cleanup.py         # TTL deletion + expire action
├── app.py             # FastAPI application factory
├── deps.py            # Request-scoped DB / config dependencies
├── routes/api.py      # REST API endpoints (JSON)
└── services/          # jobs / applied / blocklist / analytics business logic
config.yaml            # All configurable values (roles, ATS list, TTL, schedule…)
vendor/jobhive/        # Vendored jobhive scraper library + company manifests
tests/                 # Per-module test suites (pytest)
```

---

## Setup

Requires **Python 3.12** and **[uv](https://github.com/astral-sh/uv)**.

```bash
# Install dependencies (creates .venv, installs vendored jobhive editable)
uv sync --all-extras
```

The SQLite database and its schema (tables, indexes, FTS5 virtual table, sync
triggers) are created automatically on first app startup, or explicitly:

```python
from jobpulse.config import load_config
from jobpulse.database import init_db

init_db(load_config("config.yaml"))
```

## Configuration

All configurable values live in [`config.yaml`](config.yaml) — there are no hardcoded
constants. Key sections:

```yaml
target_roles:          # Job titles to match (e.g. "AI Engineer", "Backend Engineer")
ats_platforms:         # primary / secondary / low_priority tiers (scrape order)
schedule:              # 3×/day scrape times + nightly cleanup (PST), timezone
location:              # primary country (USA default; India = config switch)
data_lifecycle:        # ttl_days (default 3)
database: { path }     # SQLite file location
logging:               # level, file, max_bytes (10MB), backup_count (5)
server: { host, port }
```

Point at an alternate config with the `JOBPULSE_CONFIG` environment variable.

## Running the API

```bash
uv run uvicorn "jobpulse.app:create_app" --factory --host 0.0.0.0 --port 8000
```

Interactive API docs are then at `http://localhost:8000/docs`.

### REST API

| Method | Path | Action |
|--------|------|--------|
| GET | `/api/jobs` | List jobs (filters, search, sort, pagination) |
| GET | `/api/jobs/{id}` | Job detail |
| POST | `/api/jobs/{id}/expire` | Mark expired |
| POST | `/api/jobs/{id}/viewed` | Mark viewed (clears "New" badge) |
| POST | `/api/jobs/{id}/apply` | Move to applied tracker |
| GET | `/api/applied` | List applied jobs |
| PATCH | `/api/applied/{id}` | Update status / notes / follow-up |
| GET | `/api/blocklist` | List blocked companies |
| POST | `/api/blocklist` | Block a company |
| DELETE | `/api/blocklist/{id}` | Unblock |
| GET | `/api/analytics/summary` | All analytics data + summary cards |
| GET | `/api/scrape-runs` | Recent scrape run audit log |

**`GET /api/jobs` filters** (all combinable): `search` (FTS5), `role`, `ats`,
`location`, `remote`, `employment_type`, `posted_within_days`, `salary_min`,
`sort` (`relevance` / `posted` / `salary`), `limit`, `offset`.

## Scraping (programmatic)

Until the cron runner lands in Module 8, a scrape + ingest pass can be driven directly:

```python
from jobpulse.config import load_config
from jobpulse.database import init_db
from jobpulse.scraper import run_scrape
from jobpulse.ingest import ingest_jobs, record_scrape_run

config = load_config("config.yaml")
conn = init_db(config)

result = run_scrape(config, max_companies_per_ats=25)   # cap for a quick run
stats = ingest_jobs(conn, result.jobs, target_roles=config.target_roles)
record_scrape_run(
    conn, schedule_slot="manual", ats_types_scraped=result.ats_types,
    jobs_fetched=result.total_fetched, jobs_inserted=stats.inserted,
    jobs_updated=stats.updated, status="success",
)
```

Company slugs are read from `vendor/jobhive/ats-companies/{ats}.csv` (thousands of
companies per ATS). Jobs are filtered by title against `target_roles`, deduplicated on
`global_id`, scored for relevance, and reconciled into the `jobs` table.

## Tests

```bash
uv run pytest            # full suite
uv run pytest tests/test_module_4/ -v
```

Every module ships with a corresponding test suite covering edge cases — null fields,
boundary dates, duplicate `global_id`, blocked companies, and filter combinations.

---

## Data model

Four tables (see [`SCOPE.md`](SCOPE.md) §5 for full column definitions):

- **`jobs`** — the live feed (deduped on `global_id`, FTS5-indexed, TTL-reaped).
- **`applied_jobs`** — permanent applied tracker, excluded from TTL deletion.
- **`company_blocklist`** — companies hidden from the feed at display time.
- **`scrape_runs`** — audit log of every scrape / cleanup run.

---

## Roadmap

| Module | Scope | Status |
|--------|-------|--------|
| 0 | Dependency audit (jobhive internals) | ✅ |
| 1 | Scaffolding, config, database, logging | ✅ |
| 2 | Scraper integration & ingestion pipeline | ✅ |
| 3 | Data lifecycle & cleanup | ✅ |
| 4 | Core API & service layer | ✅ |
| 5 | Dashboard templates (job feed) | ⏳ |
| 6 | Applied / blocklist / scrape-log pages | ⏳ |
| 7 | Analytics dashboard | ⏳ |
| 8 | Cron, deployment & documentation | ⏳ |

---

## Credits

- **Source dataset & scrapers**: [jobhive / ats-scrapers](https://github.com/kalil0321/ats-scrapers)
  by kalil0321 — MIT License. JobPulse vendors and builds on this work.

## License

MIT
