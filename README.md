# JobPulse

A self-hosted, minimalistic **job search dashboard** built on top of the
[jobhive / ats-scrapers](https://github.com/kalil0321/ats-scrapers) open-source ATS
scraper library. JobPulse vendors the jobhive scrapers locally, runs them on a personal
schedule, stores results in SQLite, and serves a FastAPI dashboard for browsing,
filtering, tracking, and analyzing job applications.

> **Status:** Feature-complete (Modules 1–8). Config, database, scraper/ingestion,
> lifecycle, REST API, the full HTML dashboard (feed, applied, blocklist, scrape
> logs, analytics), the scheduler, and deployment tooling are all in place.
> See [`SCOPE.md`](SCOPE.md) for the development plan.

---

## Why

- Eliminate manual browsing of career pages, job boards, and LinkedIn.
- Surface fresh jobs on a daily scrape (configurable) before aggregators pick them up.
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
├── google_search/     # Phase 2 — Google Search discovery channel (no-driver)
├── ingest.py          # Dedup, insert/update, blocklist, scrape_runs logging
├── cleanup.py         # TTL deletion + expire action
├── pipeline.py        # Scrape/cleanup orchestration with run-lock
├── scheduler.py       # In-process cron scheduler (toggle via env)
├── app.py             # FastAPI application factory
├── deps.py            # Request-scoped DB / config dependencies
├── routes/            # api.py (JSON) + pages.py (HTML/HTMX)
├── templates/         # Jinja2 templates (feed, applied, blocklist, logs, analytics)
├── static/            # CSS + JS (HTMX actions, Chart.js)
└── services/          # jobs / applied / blocklist / analytics business logic
config.yaml            # All configurable values (roles, ATS list, TTL, schedule…)
locations.yaml         # Phase 2 — target cities for Google-search queries
.env.example           # Env overrides (JOBPULSE_CRON_ENABLED, …)
scripts/               # run_scrape.py, run_google_search.py, run_cleanup.py, crontab.example
systemd/               # jobpulse.service unit file
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
google_search:         # Phase 2 — per-run query cap, delays, cache TTL
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

## Scraping & scheduling

A scrape runs the full pipeline: fetch every configured ATS → filter by title
against `target_roles` **and by location** (`location.country_code`, US by
default) → dedup on `global_id` → score relevance → reconcile into the `jobs`
table → log the run to `scrape_runs`. Company slugs come from
`vendor/jobhive/ats-companies/{ats}.csv`; `scrape.max_companies_per_ats` caps how
many are hit per run. All runs are **idempotent** (dedup) and **serialized** by a
process-wide lock, so overlapping triggers or a restart mid-run never corrupt data.

**Location filtering** (`location` in config.yaml): scrapers return every job a
company posts worldwide, so each posting's **location field** (never the
description) is classified against the target country's **state roster** — a
`{code: full_name}` map of every state/territory. A posting matches if the
location contains a state code (`NC`), a full state name (`North Carolina`),
the country name, or a matching `country_iso`. Two-letter collisions (TN =
Tennessee/Tamil Nadu, CA = California/Canada) are resolved by tiered
precedence. Confirmed-foreign jobs are dropped at ingest and purged from the
DB on each scrape; `keep_unknown: false` also drops jobs whose country can't be
confirmed (unless remote). Rosters ship for **US** and **India** — switch with
`country_code: US` / `IN`.

There are three ways to run it:

### 1. In-process scheduler (the cron toggle)

Set the master switch in `.env` (copy from `.env.example`):

```bash
JOBPULSE_CRON_ENABLED=true     # fire the daily scrape + nightly cleanup automatically
JOBPULSE_CRON_ENABLED=false    # no automatic runs — trigger manually from the UI (dev)
```

When `true`, the app starts a background scheduler that fires at each time in
`schedule.scrape_times` (default a **single daily scrape at 05:00 America/New_York —
5 AM ET**) plus `schedule.cleanup_time` nightly, in the configured timezone. Add
more `scrape_times` entries to scrape several times a day. When `false`, nothing
runs automatically — ideal for development, where you don't want a restart kicking
off a scrape.

### 2. Manual trigger from the UI (dev)

The **Scrape Logs** page shows the scheduler state and has **Run scrape now** /
**Run cleanup now** buttons. They start the run in the background and return
immediately; a "running…" indicator appears and the run-lock blocks overlaps.
This is the recommended way to scrape during development.

### 3. OS cron (alternative to the in-process scheduler)

With `JOBPULSE_CRON_ENABLED=false`, schedule the standalone scripts via the OS:

```bash
uv run python scripts/run_scrape.py morning
uv run python scripts/run_cleanup.py
```

See [`scripts/crontab.example`](scripts/crontab.example) for ready-to-use entries
(with PST timezone notes). Don't enable both the in-process scheduler and OS cron
at once — you'd double-run.

## Google Search discovery (Phase 2)

A second discovery channel that finds fresh postings via Google Search with
`site:` operators (past-24h filter). By default it drives the **real system
Chrome via `nodriver`** (no chromedriver/Selenium) — Google reliably serves
plain HTTP a `/sorry/` + 429 CAPTCHA, but the real browser is not rate-limited.
Set `google_search.engine: "http"` to fall back to the legacy plain-httpx path.
Queries are generated **from your config** — every `target_roles` × searchable
`ats_platforms` — as `site:{domain} "{role}" "{location}"`. **US-only:** most
ATS use a single `"United States"` query per role (per-city just multiplies
near-identical queries); only **Workday** searches per US city from
[`locations.yaml`](locations.yaml). Results feed the **same `jobs` table**
with `source='google_search'`, sharing Phase 1's dedup, location filter,
blocklist, TTL, and feed. Matched ATS URLs are fetched directly (per-job JSON for
Greenhouse/Lever, schema.org JSON-LD fallback otherwise).

- **Manual** — on the **Scrape Logs** page, click **Search Internet** (no input).
  It runs a polite background batch of the configured matrix; results land in the
  feed. ATS without a Phase 2 URL parser (jazzhr, teamtailor, bamboohr, phenom)
  are skipped and logged.
- **Scheduled** — `uv run python scripts/run_google_search.py morning|afternoon|evening`
  runs the same generator restricted to that slot's ATS tiers + regions.
  See the Phase 2 lines in [`scripts/crontab.example`](scripts/crontab.example).

Each run is a **polite capped batch**: it self-stops at
`google_search.max_queries_per_run` (recorded as a `partial` run, nothing dropped
silently), and queries are shuffled so repeated clicks / cron slots accumulate
coverage cheaply against the 24h result cache. Tunables live under
`google_search:` in `config.yaml` (`engine`, `headless`, `settle_seconds`,
per-run cap, inter-query delay (default 20–45s), failure threshold, cache TTL).
The browser engine needs Chrome installed and a display — on a headless server,
set `headless: true` or use the `http` engine. The dashboard adds a **Source** filter, a **Google** badge
on those cards, and a **Google finds** analytics metric.

---

## Deployment

JobPulse runs as a single Python process — no containers, no external services.

1. **Provision** an OCI free-tier instance (1 GB RAM is plenty; x86 or ARM).
   Install Python 3.12 and [uv](https://github.com/astral-sh/uv).
2. **Clone** the repo to `/opt/jobpulse` and run `uv sync`.
3. **Configure**: copy `.env.example` → `.env`, set `JOBPULSE_CRON_ENABLED=true`,
   and review `config.yaml` (roles, ATS list, schedule, TTL, `max_companies_per_ats`).
4. **Install the service**:

   ```bash
   sudo cp systemd/jobpulse.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now jobpulse
   ```

   See [`systemd/jobpulse.service`](systemd/jobpulse.service) — it reads `.env`,
   runs uvicorn, and restarts on failure. The dashboard then serves on port 8000
   (put it behind a reverse proxy / firewall as desired).

With the service running and `JOBPULSE_CRON_ENABLED=true`, the in-process
scheduler handles all scraping and cleanup — no crontab needed.

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
- **`search_runs`** — audit log of Phase 2 Google-search runs.
- **`search_results_cache`** — short-lived (query, url) cache to skip re-fetching (24h TTL).

---

## Roadmap

| Module | Scope | Status |
|--------|-------|--------|
| 0 | Dependency audit (jobhive internals) | ✅ |
| 1 | Scaffolding, config, database, logging | ✅ |
| 2 | Scraper integration & ingestion pipeline | ✅ |
| 3 | Data lifecycle & cleanup | ✅ |
| 4 | Core API & service layer | ✅ |
| 5 | Dashboard templates (job feed) | ✅ |
| 6 | Applied / blocklist / scrape-log pages | ✅ |
| 7 | Analytics dashboard | ✅ |
| 8 | Cron, deployment & documentation | ✅ |
| P2 | Google Search discovery channel (engine, manual search, cron, dashboard) | ✅ |

---

## Credits

- **Source dataset & scrapers**: [jobhive / ats-scrapers](https://github.com/kalil0321/ats-scrapers)
  by kalil0321 — MIT License. JobPulse vendors and builds on this work.

## License

MIT
