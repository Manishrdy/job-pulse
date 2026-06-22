# Module 0: jobhive Dependency Audit

> Completed: 2026-06-22
> Source: `vendor/jobhive/` (cloned from https://github.com/kalil0321/ats-scrapers)

---

## 1. Scraper API

### How it works

Each ATS has a scraper class registered via `@ScraperRegistry.register(ATSType.X)`. All scrapers extend `BaseScraper` and implement one method:

```python
class BaseScraper(ABC):
    ats: ClassVar[ATSType]

    def __init__(self, company_slug: str, *, timeout: float = 30.0) -> None:
        self.company_slug = company_slug
        self.timeout = timeout
        self.include_descriptions = True

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return all currently active jobs for this company."""
```

### Usage pattern

```python
from jobhive.scrapers import get_scraper, ScraperRegistry
from jobhive.models import ATSType

# Option A: direct instantiation
scraper = GreenhouseScraper("openai")
jobs = scraper.fetch()  # -> list[Job]

# Option B: registry lookup
scraper = get_scraper(ATSType.GREENHOUSE, "openai")
jobs = scraper.fetch()

# Option C: iterate all registered scrapers
all_scrapers = ScraperRegistry.all()  # -> dict[ATSType, type[BaseScraper]]
```

### Key behaviors

- `fetch()` returns `list[Job]` — all active jobs for one company on one ATS.
- Each scraper handles its own HTTP retries (3 attempts, exponential backoff).
- Raises `ScraperError` on HTTP failures, `CompanyNotFoundError` on 404.
- Descriptions included by default (`include_descriptions = True`). Some scrapers (Greenhouse) fetch inline; others (Workday) need per-job detail fetches via `get_description()` / `enrich_descriptions()`.
- Internally async (httpx.AsyncClient) but `fetch()` is sync (calls `asyncio.run()`).

### Company discovery

jobhive ships `ats-companies/*.csv` files — one per ATS platform. Format: `name,slug,url`.

| ATS | Companies |
|-----|-----------|
| Greenhouse | 4,966 |
| Ashby | 2,856 |
| Lever | 2,113 |
| iCIMS | has CSV |
| SmartRecruiters | has CSV |
| JazzHR | has CSV |
| Teamtailor | has CSV |
| Gem | has CSV |
| BambooHR | has CSV |
| Workable | has CSV |
| Rippling | has CSV |
| Phenom | has CSV |
| Workday | has CSV |

We iterate these CSVs to get company slugs, then call `scraper.fetch()` per company. Filtering by role title happens post-fetch (match against `job.title`).

---

## 2. jobhive `Job` Model → Our `jobs` Table Mapping

### Direct 1:1 mappings

| jobhive `Job` field | Our `jobs` column | Type | Notes |
|---------------------|-------------------|------|-------|
| `global_id` | `global_id` | TEXT | Auto-computed: `{ats_type}:{ats_id}`. UUID4 fallback on bad ats_id. |
| `url` | `url` | TEXT | HttpUrl — cast to str. Always present. |
| `apply_url` | `apply_url` | TEXT | HttpUrl or None — cast to str. |
| `title` | `title` | TEXT | Always present. |
| `company` | `company` | TEXT | Always present. Note: Greenhouse uses slug, not display name. |
| `ats_type` | `ats_type` | TEXT | ATSType enum `.value` (e.g. "greenhouse"). |
| `ats_id` | `ats_id` | TEXT | May be None → global_id uses UUID4. |
| `location` | `location` | TEXT | Free-form. Nullable. |
| `country_iso` | `country_iso` | TEXT | ISO 3166-1 alpha-2. Rarely populated by scrapers. |
| `is_remote` | `is_remote` | INTEGER | bool→int (0/1/NULL). True only when confirmed. |
| `salary_min` | `salary_min` | REAL | Nullable. |
| `salary_max` | `salary_max` | REAL | Nullable. |
| `salary_currency` | `salary_currency` | TEXT | ISO 4217. Nullable. |
| `salary_period` | `salary_period` | TEXT | HOUR/DAY/WEEK/MONTH/YEAR. Nullable. |
| `salary_summary` | `salary_summary` | TEXT | Original string. Nullable. |
| `employment_type` | `employment_type` | TEXT | FULL_TIME/PART_TIME/CONTRACT/INTERN/TEMPORARY. Nullable. |
| `department` | `department` | TEXT | Nullable. |
| `team` | `team` | TEXT | Nullable. |
| `experience` | `experience` | INTEGER | Nullable. |
| `description` | `description` | TEXT | Plain text or HTML (up to 25k chars). Nullable. |
| `posted_at` | `posted_at` | TIMESTAMP | UTC. Nullable (many ATSes don't expose it). |
| `language` | `language` | TEXT | ISO 639-1. Nullable. |
| `requisition_id` | `requisition_id` | TEXT | Nullable. |

### jobhive fields we DROP (not in our schema)

| Field | Reason |
|-------|--------|
| `region` | Continent-level grouping, not useful for US job search. |
| `lat`, `lon` | Geocoding — rarely populated, not needed for our dashboard. |
| `commitment` | Free-form ATS label; we already have `employment_type`. |
| `fetched_at` | We use `first_seen`/`last_seen` instead. |
| `raw` | Provider-specific overflow JSON. Not needed for dashboard. |

### Our fields NOT in jobhive (added by us)

| Column | Purpose |
|--------|---------|
| `id` | INTEGER PK AUTOINCREMENT |
| `first_seen` | When our scraper first found it |
| `last_seen` | Last scrape run that found it |
| `viewed_at` | When user first opened card in dashboard |
| `status` | active / expired |
| `expired_at` | When user marked expired |
| `is_blocked` | 1 if company is in blocklist |
| `relevance_score` | FTS5 keyword match score |

### Type conversions needed

1. `url` and `apply_url`: `HttpUrl` → `str(job.url)`
2. `is_remote`: `bool | None` → `int | None` (True→1, False→0, None→NULL)
3. `ats_type`: `ATSType` enum → `.value` string
4. `posted_at`: `datetime | None` → ISO string or NULL
5. `company`: For Greenhouse, the scraper sets `company=self.company_slug` (e.g. "openai"), not the display name. We should resolve from the CSV manifest's `name` column.

---

## 3. Enrichment Module

Two pure functions available:

1. **`infer_is_remote(title)`** — Returns `True` if title contains remote keywords ("remote", "anywhere", "wfh", "telework", "work from home"). Returns `None` (not `False`) otherwise.

2. **`parse_salary_range(text)`** — Regex-based extraction from `salary_summary` strings. Returns `(min, max)` floats or `(None, None)`. Handles `$120K – $160K`, `€80k–€120k`, `CA$400K – CA$500K`, etc.

Both are useful at ingest time to fill gaps.

---

## 4. Dependencies Required

From jobhive's `pyproject.toml`, the `[scrapers]` extra needs:

```
httpx>=0.27
pydantic>=2.6
aiohttp>=3.9
beautifulsoup4>=4.12
html2text>=2024.0
httpcloak>=1.6        # TLS impersonation for Avature, JazzHR, Eightfold
cloakbrowser>=0.3     # Stealth Chromium for Tesla, Meta
```

For our project, we need:
- `httpx`, `pydantic` — required by all scrapers
- `aiohttp`, `beautifulsoup4`, `html2text` — required by several scrapers
- `httpcloak` — needed only for Avature/JazzHR/Eightfold (JazzHR is in our list)
- `cloakbrowser` — needed only for Tesla/Meta (NOT in our target list — skip)
- `pandas` — required by jobhive base but we don't use `Client`. Can include for safety.

Our additional deps:
- `fastapi`, `uvicorn` — web server
- `jinja2` — templates
- `pyyaml` — config loading
- `aiosqlite` or just `sqlite3` stdlib — database

---

## 5. Architecture Decision: How We Scrape

The scrapers work **per-company** — `GreenhouseScraper("openai").fetch()` returns all jobs at OpenAI on Greenhouse. There's no "search by role title" API parameter.

**Our approach:**
1. Load company slugs from `vendor/jobhive/ats-companies/{ats}.csv`
2. For each company, call `scraper.fetch()` → get all jobs
3. Filter by title match against our `target_roles` config
4. Dedup on `global_id`, insert/update in SQLite

**Implication:** Scraping ~5,000 Greenhouse companies × 3x/day is expensive and slow. We need to either:
- (a) Maintain a curated subset of companies known to hire for our target roles, OR
- (b) Scrape all companies but accept it takes 30-60 minutes per run

This is a config/tuning decision for Module 2, not a blocker for Module 1.

---

## 6. Workday Special Case

Workday scrapers accept a full careers URL as `company_slug` (not just a slug):
```
https://accenture.wd3.myworkdayjobs.com/AccentureCareers
```
The scraper parses out `company=accenture`, `instance=wd3`, `site=AccentureCareers`.

Workday also paginates at 20 items/page with a 2,000 item cap per query. Large tenants need facet-based subdivision. The scraper handles this internally.

---

*Module 0 complete. Ready to begin Module 1: Project Scaffolding & Configuration.*
