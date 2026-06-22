# JobPulse — Personal Job Search Dashboard

> **Scope & Development Guide v1.0**
> Date: 2026-06-22
> Author: Chaitanya Alla

---

## 1. Project Overview

JobPulse is a self-hosted, minimalistic job search dashboard built on top of the [jobhive](https://github.com/kalil0321/ats-scrapers) open-source ATS scraper dataset. It clones the jobhive pipeline, runs it on a personal schedule, stores results in SQLite, and serves a FastAPI + Jinja2 dashboard for browsing, filtering, tracking, and analyzing job applications.

### 1.1 Goals

- Eliminate manual browsing of career pages, job boards, and LinkedIn.
- Surface fresh jobs 3x/day before aggregators pick them up.
- Track applications, blocklist companies, and maintain personal analytics.
- Keep the system minimal — single binary-style deployment on OCI.

### 1.2 Credits & Licensing

- **Source dataset & scrapers**: [jobhive / ats-scrapers](https://github.com/kalil0321/ats-scrapers) by kalil0321 — MIT License.
- **This project**: MIT License. Must include attribution to the jobhive project in README and LICENSE.

---

## 2. Functional Requirements

### FR-01: Job Ingestion Pipeline

| ID | Requirement |
|----|-------------|
| FR-01.1 | Clone and integrate jobhive scraper library as a dependency (`jobhive-py[scrapers]`). |
| FR-01.2 | Scrape jobs for a configurable list of target roles (see §4.1). |
| FR-01.3 | Run on cron schedule — 3x/day: morning (8AM PST), afternoon (1PM PST), evening (6PM PST). |
| FR-01.4 | On each run, scrape target ATS platforms in priority order (see §4.2). |
| FR-01.5 | Dedup on `global_id` (`{ats_type}:{ats_id}`). If exists, update `last_seen`. If new, insert with `first_seen = now()`. |
| FR-01.6 | Compute a relevance score at ingest time using FTS5 keyword matching against preferred role terms. |
| FR-01.7 | Log each scrape run to `scrape_runs` table — `run_at`, `jobs_fetched`, `jobs_inserted`, `jobs_updated`, `status`, `error_msg`, `duration_seconds`. |
| FR-01.8 | Skip/hide jobs from companies in the blocklist during ingestion (still scrape, just mark `is_blocked = 1`). |

### FR-02: Dashboard — Job Feed

| ID | Requirement |
|----|-------------|
| FR-02.1 | Default view: all active, non-blocked, non-expired jobs sorted by `posted_at DESC NULLS LAST`, then `first_seen DESC`. |
| FR-02.2 | Job card displays: title, company, ATS type, location, remote badge, salary range (if available), posted date / first seen, relevance score. |
| FR-02.3 | Each card has: "Apply" (external link to `apply_url` or `url`), "Mark Applied", "Expire", "Block Company" action buttons. |
| FR-02.4 | Clicking a job card expands to show full description. |
| FR-02.5 | "New" badge on jobs not yet viewed. Track via `viewed_at` timestamp, cleared when card is expanded/clicked. |

### FR-03: Dashboard — Filters & Search

| ID | Requirement |
|----|-------------|
| FR-03.1 | Full-text search across title, company, description using SQLite FTS5. |
| FR-03.2 | Filter by: role/title keyword, ATS platform, city/location text, remote only, employment type. |
| FR-03.3 | Filter by time posted: 1d, 2d, 3d (based on `posted_at`, falling back to `first_seen`). |
| FR-03.4 | Filter by minimum salary (when `salary_min` is available). |
| FR-03.5 | All filters combinable. URL query params preserve filter state on refresh. |
| FR-03.6 | Sort options: relevance score, posted date, salary (desc). |

### FR-04: Applied Jobs Tracker

| ID | Requirement |
|----|-------------|
| FR-04.1 | "Mark Applied" moves a job from `jobs` to `applied_jobs` table with `applied_at = now()`. |
| FR-04.2 | Applied jobs page with its own search and filters. |
| FR-04.3 | Each applied job has: status dropdown (Applied, Phone Screen, Interview, Offer, Rejected, Ghosted), notes text field, follow-up date. |
| FR-04.4 | Applied table is excluded from the global delete policy — records persist indefinitely. |

### FR-05: Company Blocklist

| ID | Requirement |
|----|-------------|
| FR-05.1 | "Block Company" adds company to `company_blocklist` with `blocked_at`, optional `reason`. |
| FR-05.2 | All jobs from blocked companies hidden from main feed immediately. |
| FR-05.3 | Blocklist management page — view all blocked companies, unblock option. |
| FR-05.4 | Blocklist applies at display time (jobs still scraped and stored, just filtered out). |

### FR-06: Data Lifecycle & Cleanup

| ID | Requirement |
|----|-------------|
| FR-06.1 | Global delete policy: delete all jobs from `jobs` table where `first_seen` is older than 3 days. Run daily. |
| FR-06.2 | `applied_jobs` table excluded from delete policy. |
| FR-06.3 | "Expire" button on job card sets `status = 'expired'` and `expired_at = now()`. Expired jobs hidden from default feed. |
| FR-06.4 | Expired jobs are still subject to the 3-day delete policy. |

### FR-07: Analytics Dashboard

| ID | Requirement |
|----|-------------|
| FR-07.1 | Role-wise breakdown: how many jobs seen, how many applied per role keyword. |
| FR-07.2 | Day-wise application count: calendar/bar chart of applications per day. |
| FR-07.3 | ATS platform breakdown: jobs per ATS, applications per ATS. |
| FR-07.4 | Application status funnel: Applied → Phone Screen → Interview → Offer / Rejected / Ghosted. |
| FR-07.5 | Scraped job trends: jobs fetched per day over time. |

---

## 3. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-01 | **Stack**: Python 3.11+, FastAPI, Jinja2, SQLite3, jobhive-py. |
| NFR-02 | **Deployment**: Single process on OCI instance. Systemd service or PM2. |
| NFR-03 | **Database**: SQLite with WAL mode enabled for concurrent read/write. |
| NFR-04 | **Logging**: Structured logging via Python `logging` module. Rotating file handler, max 10MB per file, 5 backup files. All scrape runs, errors, data mutations logged. |
| NFR-05 | **Testing**: Every module must have corresponding test file. Pytest. Cover all edge cases — null fields, empty descriptions, duplicate `global_id`, blocked companies, date boundary conditions. |
| NFR-06 | **Config**: All configurable values (roles, ATS list, cron schedule, TTL days, location) in a single `config.yaml` or `.env` file. No hardcoded constants. |
| NFR-07 | **Performance**: Dashboard page load < 500ms for up to 50,000 jobs in SQLite. FTS5 search < 200ms. |
| NFR-08 | **Git hygiene**: Strict `.gitignore` (no `.db` files, no `__pycache__`, no `.env`). Conventional commits. |
| NFR-09 | **README.md**: Full setup instructions, config guide, cron setup, OCI deployment notes, credits to jobhive. |
| NFR-10 | **Location scope**: USA (primary), India (compatible — config switch, no code change). |

---

## 4. Configuration Defaults

### 4.1 Target Roles

```yaml
target_roles:
  - "AI Engineer"
  - "Gen AI Engineer"
  - "Generative AI Engineer"
  - "Forward Deployed Engineer"
  - "Software Engineer"
  - "Senior Software Engineer"
  - "Software Development Engineer"
  - "Backend Engineer"
```

### 4.2 ATS Platform Priority

| Tier | ATS Platforms | Scrape Priority |
|------|--------------|-----------------|
| **Primary** | Greenhouse, Lever, Ashby, iCIMS | Always scrape first. Highest relevance weight. |
| **Secondary** | SmartRecruiters, JazzHR, Teamtailor, Gem, BambooHR, Workable, Rippling, Phenom | Scrape after primary. Standard weight. |
| **Low Priority** | Workday | Scrape last. Lowest weight. Include but deprioritize in feed. |

### 4.3 Cron Schedule

```
# PST times (convert to server timezone on OCI)
0 8 * * *    # Morning run
0 13 * * *   # Afternoon run
0 18 * * *   # Evening / EOD run
0 2 * * *    # Nightly cleanup (3-day TTL delete)
```

### 4.4 Location

```yaml
location:
  primary: "United States"
  future: "India"       # switch when ready, no code change needed
  remote_preferred: true
```

---

## 5. Database Schema

### 5.1 Entity Relationship

```
┌─────────────┐       ┌──────────────────┐       ┌────────────────────┐
│  jobs        │──────→│  applied_jobs     │       │ company_blocklist   │
│  (live feed) │ move  │  (permanent)      │       │ (filter)            │
└─────────────┘       └──────────────────┘       └────────────────────┘
       │
       │ logged by
       ▼
┌─────────────────┐
│  scrape_runs     │
│  (audit log)     │
└─────────────────┘
```

### 5.2 Table: `jobs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK, AUTOINCREMENT | Internal row ID |
| `global_id` | TEXT | UNIQUE, NOT NULL | `{ats_type}:{ats_id}` — dedup key |
| `url` | TEXT | NOT NULL | Public posting URL |
| `apply_url` | TEXT | NULLABLE | Direct application URL if different from `url` |
| `title` | TEXT | NOT NULL | Job title as posted |
| `company` | TEXT | NOT NULL | Employer display name |
| `ats_type` | TEXT | NOT NULL | ATS platform identifier |
| `ats_id` | TEXT | NULLABLE | ATS-internal posting ID |
| `location` | TEXT | NULLABLE | Free-form location string |
| `country_iso` | TEXT | NULLABLE | ISO 3166-1 alpha-2 |
| `is_remote` | INTEGER | NULLABLE | 0/1/NULL |
| `salary_min` | REAL | NULLABLE | Lower bound in `salary_currency` |
| `salary_max` | REAL | NULLABLE | Upper bound |
| `salary_currency` | TEXT | NULLABLE | ISO 4217 |
| `salary_period` | TEXT | NULLABLE | HOUR/DAY/WEEK/MONTH/YEAR |
| `salary_summary` | TEXT | NULLABLE | Original salary string |
| `employment_type` | TEXT | NULLABLE | FULL_TIME/PART_TIME/CONTRACT/INTERN/TEMPORARY |
| `department` | TEXT | NULLABLE | High-level org group |
| `team` | TEXT | NULLABLE | Sub-team / squad |
| `experience` | INTEGER | NULLABLE | Required years |
| `description` | TEXT | NULLABLE | Plain-text JD |
| `posted_at` | TIMESTAMP | NULLABLE | ATS-reported publish time (UTC) |
| `first_seen` | TIMESTAMP | NOT NULL, DEFAULT NOW | When our scraper first saw it |
| `last_seen` | TIMESTAMP | NOT NULL, DEFAULT NOW | Last scrape run that found it |
| `viewed_at` | TIMESTAMP | NULLABLE | When user first opened this card |
| `status` | TEXT | NOT NULL, DEFAULT 'active' | active / expired |
| `expired_at` | TIMESTAMP | NULLABLE | When user marked expired |
| `is_blocked` | INTEGER | NOT NULL, DEFAULT 0 | 1 if company is in blocklist |
| `relevance_score` | REAL | NOT NULL, DEFAULT 0.0 | FTS5/keyword match score |
| `language` | TEXT | NULLABLE | ISO 639-1 |
| `requisition_id` | TEXT | NULLABLE | Employer-internal req ID |

**Indexes:**
- `idx_jobs_global_id` on `global_id` (UNIQUE)
- `idx_jobs_company` on `company`
- `idx_jobs_posted_at` on `posted_at`
- `idx_jobs_first_seen` on `first_seen`
- `idx_jobs_status` on `status`
- `idx_jobs_ats_type` on `ats_type`

### 5.3 Table: `jobs_fts` (FTS5 Virtual Table)

```sql
CREATE VIRTUAL TABLE jobs_fts USING fts5(
    title,
    company,
    description,
    location,
    content='jobs',
    content_rowid='id'
);
```

Kept in sync via triggers on `jobs` INSERT/UPDATE/DELETE.

### 5.4 Table: `applied_jobs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `global_id` | TEXT | UNIQUE, NOT NULL | Carried from `jobs` |
| `url` | TEXT | NOT NULL | |
| `apply_url` | TEXT | NULLABLE | |
| `title` | TEXT | NOT NULL | |
| `company` | TEXT | NOT NULL | |
| `ats_type` | TEXT | NOT NULL | |
| `location` | TEXT | NULLABLE | |
| `is_remote` | INTEGER | NULLABLE | |
| `salary_summary` | TEXT | NULLABLE | |
| `employment_type` | TEXT | NULLABLE | |
| `description` | TEXT | NULLABLE | |
| `posted_at` | TIMESTAMP | NULLABLE | |
| `first_seen` | TIMESTAMP | NOT NULL | |
| `applied_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | When user marked applied |
| `status` | TEXT | NOT NULL, DEFAULT 'applied' | applied / phone_screen / interview / offer / rejected / ghosted |
| `notes` | TEXT | NULLABLE | Free-form notes |
| `follow_up_date` | DATE | NULLABLE | Reminder date |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | Last status/notes change |

**Indexes:**
- `idx_applied_global_id` on `global_id` (UNIQUE)
- `idx_applied_status` on `status`
- `idx_applied_applied_at` on `applied_at`
- `idx_applied_company` on `company`

### 5.5 Table: `company_blocklist`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `company` | TEXT | UNIQUE, NOT NULL | Exact company name match |
| `blocked_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | |
| `reason` | TEXT | NULLABLE | "Rejected previously", "Racial discrimination", etc. |

### 5.6 Table: `scrape_runs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `run_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | |
| `schedule_slot` | TEXT | NULLABLE | morning / afternoon / evening / manual |
| `ats_types_scraped` | TEXT | NOT NULL | Comma-separated list |
| `jobs_fetched` | INTEGER | NOT NULL, DEFAULT 0 | Total from scrapers |
| `jobs_inserted` | INTEGER | NOT NULL, DEFAULT 0 | New jobs added |
| `jobs_updated` | INTEGER | NOT NULL, DEFAULT 0 | Existing jobs with `last_seen` bump |
| `jobs_deleted` | INTEGER | NOT NULL, DEFAULT 0 | Cleanup deletes this run |
| `duration_seconds` | REAL | NULLABLE | |
| `status` | TEXT | NOT NULL | success / partial_failure / failure |
| `error_msg` | TEXT | NULLABLE | |

---

## 6. Analytics Queries (Reference)

These are the core analytics the dashboard must support:

```
-- FR-07.1: Role-wise breakdown
SELECT matched_role, COUNT(*) as seen, SUM(CASE WHEN applied=1 THEN 1 ELSE 0 END) as applied
FROM analytics_view GROUP BY matched_role;

-- FR-07.2: Day-wise applications
SELECT DATE(applied_at) as day, COUNT(*) FROM applied_jobs GROUP BY day ORDER BY day DESC;

-- FR-07.3: ATS breakdown
SELECT ats_type, COUNT(*) FROM jobs WHERE status='active' GROUP BY ats_type;

-- FR-07.4: Application funnel
SELECT status, COUNT(*) FROM applied_jobs GROUP BY status;

-- FR-07.5: Scrape trends
SELECT DATE(run_at), SUM(jobs_inserted) FROM scrape_runs GROUP BY DATE(run_at);
```

---

## 7. Development Modules (Sequential Order)

Each module must be completed and tested before starting the next. AI coding agent must follow this exact sequence.

---

### Module 1: Project Scaffolding & Configuration

**Scope:** Project structure, config loading, logging setup, SQLite initialization.

**Deliverables:**
- `/jobpulse/` — main package directory
- `config.yaml` — all configurable values (roles, ATS list, cron times, TTL, location)
- `config.py` — YAML loader with Pydantic validation
- `database.py` — SQLite connection manager, WAL mode, schema migration (create all tables + FTS5 + triggers + indexes)
- `logger.py` — rotating file handler (10MB max, 5 backups), structured format with timestamp, module, level, message
- `.gitignore` — comprehensive (*.db, *.db-wal, *.db-shm, __pycache__, .env, *.pyc, .venv/, logs/, *.log)
- `pyproject.toml` — dependencies, metadata
- `README.md` — placeholder (filled in Module 8)

**Tests (`tests/test_module_1/`):**
- Config loads correctly with defaults
- Config validates — missing required fields raise errors
- SQLite DB created with all tables, indexes, FTS5 virtual table
- FTS5 triggers fire correctly on insert/update/delete
- Logger writes to file, rotates at 10MB
- WAL mode is enabled

---

### Module 2: Scraper Integration & Ingestion Pipeline

**Scope:** jobhive scraper integration, dedup logic, relevance scoring, blocklist filtering.

**Deliverables:**
- `scraper.py` — wraps jobhive scrapers, iterates ATS platforms in priority order, fetches jobs for configured roles
- `ingest.py` — dedup on `global_id`, insert new / update `last_seen` on existing, compute `relevance_score`, set `is_blocked` if company in blocklist
- `scoring.py` — FTS5-based relevance scoring against target role keywords
- `models.py` — Pydantic models for internal Job representation (maps from jobhive `Job` to our schema)

**Tests (`tests/test_module_2/`):**
- New job inserts correctly with all fields mapped
- Duplicate `global_id` updates `last_seen`, does not create new row
- Blocked company jobs inserted with `is_blocked = 1`
- Relevance score > 0 for matching titles, = 0 for non-matching
- Handles null `description`, null `posted_at`, null `salary_*` gracefully
- Handles malformed/empty `ats_id` (UUID4 fallback)
- ATS priority order respected (primary scraped first)
- Scrape run logged to `scrape_runs` with correct counts

---

### Module 3: Data Lifecycle & Cleanup

**Scope:** TTL-based deletion, expire action, cleanup cron logic.

**Deliverables:**
- `cleanup.py` — delete jobs where `first_seen` < 3 days ago, skip `applied_jobs`, log deletions
- Expire logic in `ingest.py` or `jobs_service.py` — set `status='expired'`, `expired_at=now()`

**Tests (`tests/test_module_3/`):**
- Jobs older than 3 days deleted
- Jobs exactly 3 days old NOT deleted (boundary)
- Jobs at 3 days + 1 second deleted (boundary)
- Applied jobs never deleted regardless of age
- Expired jobs still subject to 3-day TTL
- Cleanup logs correct `jobs_deleted` count in `scrape_runs`

---

### Module 4: Core API & Job Service Layer

**Scope:** FastAPI app, job CRUD operations, applied jobs, blocklist, all business logic as service functions.

**Deliverables:**
- `app.py` — FastAPI app initialization, middleware, static files mount
- `services/jobs_service.py` — list jobs (with filters, sort, pagination), get job detail, expire job, mark viewed
- `services/applied_service.py` — mark applied (move to `applied_jobs`), update status, update notes, list applied
- `services/blocklist_service.py` — add company, remove company, list blocked
- `services/analytics_service.py` — all analytics queries from §6
- `routes/api.py` — REST endpoints for all service operations (JSON responses for HTMX/fetch calls from templates)

**API Endpoints:**

| Method | Path | Action |
|--------|------|--------|
| GET | `/api/jobs` | List jobs with filters |
| GET | `/api/jobs/{id}` | Job detail |
| POST | `/api/jobs/{id}/expire` | Mark expired |
| POST | `/api/jobs/{id}/viewed` | Mark viewed |
| POST | `/api/jobs/{id}/apply` | Move to applied |
| GET | `/api/applied` | List applied jobs |
| PATCH | `/api/applied/{id}` | Update status/notes/follow-up |
| GET | `/api/blocklist` | List blocked companies |
| POST | `/api/blocklist` | Add company |
| DELETE | `/api/blocklist/{id}` | Remove company |
| GET | `/api/analytics/summary` | All analytics data |
| GET | `/api/scrape-runs` | Recent scrape run logs |

**Tests (`tests/test_module_4/`):**
- Job listing returns correct sort order (posted_at DESC NULLS LAST, then first_seen DESC)
- Filters work: role, ATS, location text, remote, employment type, salary min, time posted
- Combined filters return correct intersection
- Blocked companies excluded from default listing
- Expired jobs excluded from default listing
- Mark applied moves job to `applied_jobs`, removes from `jobs`
- Applied status update persists
- Blocklist add immediately hides jobs from that company
- Blocklist remove unhides jobs
- Analytics queries return correct aggregates
- Pagination works correctly
- FTS5 search returns ranked results

---

### Module 5: Dashboard Templates (Job Feed)

**Scope:** Jinja2 templates for the main job feed, job cards, filters panel.

**Deliverables:**
- `templates/base.html` — layout, nav (Feed, Applied, Analytics, Blocklist, Scrape Logs)
- `templates/feed.html` — main job feed with filter sidebar
- `templates/components/job_card.html` — individual job card with action buttons
- `templates/components/filters.html` — filter panel (search box, role, ATS, location, remote toggle, time posted, salary min, sort)
- `static/css/style.css` — clean, minimal styling
- `static/js/main.js` — filter form submission, HTMX or fetch for actions (expire, apply, block)
- `routes/pages.py` — page route handlers rendering templates

**Pages:**

| Route | Template | Description |
|-------|----------|-------------|
| `/` | `feed.html` | Main job feed with filters |
| `/job/{id}` | `job_detail.html` | Full job view with description |

**Tests (`tests/test_module_5/`):**
- Feed page returns 200
- Feed page contains job cards
- Filter params reflected in rendered page
- Empty state shown when no jobs match
- Job card displays all required fields
- Action buttons present and wired to correct endpoints

---

### Module 6: Dashboard Templates (Applied, Blocklist, Scrape Logs)

**Scope:** Remaining dashboard pages.

**Deliverables:**
- `templates/applied.html` — applied jobs table with status dropdown, notes, search
- `templates/blocklist.html` — blocklist management table
- `templates/scrape_logs.html` — recent scrape runs table
- `templates/components/applied_card.html` — applied job row with inline edit

**Pages:**

| Route | Template | Description |
|-------|----------|-------------|
| `/applied` | `applied.html` | Applied jobs tracker |
| `/blocklist` | `blocklist.html` | Company blocklist management |
| `/scrape-logs` | `scrape_logs.html` | Scrape run audit log |

**Tests (`tests/test_module_6/`):**
- Applied page returns 200, shows applied jobs
- Status dropdown updates via API call
- Notes field saves
- Blocklist page shows blocked companies
- Unblock action works
- Scrape logs page shows recent runs with correct data

---

### Module 7: Analytics Dashboard

**Scope:** Analytics page with charts and summary stats.

**Deliverables:**
- `templates/analytics.html` — analytics dashboard
- Charts (Chart.js or lightweight JS charting via CDN):
  - Applications per day (bar chart)
  - Application status funnel (horizontal bar / funnel)
  - Jobs by ATS platform (donut/pie)
  - Role-wise breakdown (table)
  - Scrape trend (line chart — jobs fetched per day)
- Summary cards: total active jobs, total applied, applications this week, response rate

**Tests (`tests/test_module_7/`):**
- Analytics page returns 200
- Analytics API returns correct JSON structure
- Empty state handled (no applied jobs yet)
- Date range filtering works

---

### Module 8: Cron, Deployment & Documentation

**Scope:** Cron runner script, OCI deployment config, README, final polish.

**Deliverables:**
- `scripts/run_scrape.py` — standalone script for cron invocation (handles logging, error capture, scrape_runs entry)
- `scripts/run_cleanup.py` — standalone TTL cleanup script for nightly cron
- `crontab.example` — example crontab entries with PST timezone notes
- `systemd/jobpulse.service` — systemd unit file for FastAPI server
- `README.md` — complete:
  - Project description and motivation
  - Credits and attribution to jobhive (with link)
  - Setup instructions (Python, pip, config)
  - Database initialization
  - Cron setup
  - OCI deployment guide
  - Configuration reference
  - License (MIT)
- `LICENSE` — MIT license file

**Tests (`tests/test_module_8/`):**
- Scrape script runs end-to-end with test config
- Cleanup script deletes correct jobs
- Config example is valid YAML
- README renders correctly as markdown

---

## 8. Project Directory Structure

```
jobpulse/
├── config.yaml
├── config.py
├── database.py
├── logger.py
├── models.py
├── scraper.py
├── ingest.py
├── scoring.py
├── cleanup.py
├── app.py
├── services/
│   ├── __init__.py
│   ├── jobs_service.py
│   ├── applied_service.py
│   ├── blocklist_service.py
│   └── analytics_service.py
├── routes/
│   ├── __init__.py
│   ├── api.py
│   └── pages.py
├── templates/
│   ├── base.html
│   ├── feed.html
│   ├── job_detail.html
│   ├── applied.html
│   ├── blocklist.html
│   ├── analytics.html
│   ├── scrape_logs.html
│   └── components/
│       ├── job_card.html
│       ├── filters.html
│       └── applied_card.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
├── scripts/
│   ├── run_scrape.py
│   ├── run_cleanup.py
│   └── crontab.example
├── systemd/
│   └── jobpulse.service
├── tests/
│   ├── conftest.py          # shared fixtures (test DB, test config)
│   ├── test_module_1/
│   ├── test_module_2/
│   ├── test_module_3/
│   ├── test_module_4/
│   ├── test_module_5/
│   ├── test_module_6/
│   ├── test_module_7/
│   └── test_module_8/
├── logs/                     # gitignored
├── .gitignore
├── .env.example
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 9. Development Rules for AI Coding Agent

1. **Sequential only.** Complete Module N before starting Module N+1.
2. **Tests first.** For every code file, write the corresponding test file immediately. No code is "done" without tests.
3. **Edge cases mandatory.** Every test module must cover: null/missing fields, empty results, boundary dates, duplicate data, malformed input.
4. **Existing tests must pass.** Before any code modification, run `pytest` on all existing tests. If any fail after your change, fix before proceeding.
5. **Logging everywhere.** Every function that touches the database, makes an HTTP call, or handles user input must log at appropriate level (INFO for operations, WARNING for skips, ERROR for failures).
6. **Log rotation.** Max file size 10MB, 5 backup files. Configure in Module 1, never change.
7. **No hardcoded values.** All constants come from `config.yaml`. If you need a new constant, add it to config with a sensible default.
8. **Git hygiene.** `.gitignore` must be comprehensive from Module 1. No database files, no logs, no cache, no environment files committed.
9. **README.md** must be detailed, include license (MIT), and credit [jobhive/ats-scrapers](https://github.com/kalil0321/ats-scrapers) prominently.
10. **SQLite WAL mode** enabled at connection time. All queries parameterized (no string concatenation).

---

## 10. Future Phases (Out of Scope for Phase 1)

- Semantic/vector search upgrade (FAISS + embedding model)
- LLM-powered resume-to-JD gap analysis
- Auto-apply via Playwright
- Telegram notifications for high-relevance jobs
- India location switch and multi-region support
- Multi-user support
- Job description enrichment via LLM (country inference, remote classification)

---

*End of scope document.*
