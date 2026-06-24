from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# HH:MM in 24-hour form (00:00–23:59).
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ATSPlatforms(BaseModel):
    primary: list[str] = Field(min_length=1)
    secondary: list[str] = []
    low_priority: list[str] = []

    @property
    def all_platforms(self) -> list[str]:
        return self.primary + self.secondary + self.low_priority


class Schedule(BaseModel):
    # One scrape per time listed (24h HH:MM, in `timezone`). One entry = once
    # a day; add more entries to scrape multiple times a day.
    scrape_times: list[str] = Field(default_factory=lambda: ["05:00"])
    cleanup_time: str = "02:00"
    timezone: str = "America/New_York"

    @field_validator("scrape_times")
    @classmethod
    def _valid_scrape_times(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("scrape_times must list at least one HH:MM time")
        for t in v:
            if not _TIME_RE.match(t):
                raise ValueError(f"invalid scrape time {t!r}; expected 24h HH:MM")
        return v

    @field_validator("cleanup_time")
    @classmethod
    def _valid_cleanup_time(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(f"invalid cleanup_time {v!r}; expected 24h HH:MM")
        return v


class Location(BaseModel):
    primary: str = "United States"
    # ISO 3166-1 alpha-2 of the country to keep jobs for. Text-based location
    # rules are US-specialized; other codes fall back to ISO-only matching.
    country_code: str = "US"
    remote_preferred: bool = True
    # Keep jobs whose country can't be determined (bare "Remote", empty
    # location). False = strict: drop unless a confirmed-remote role.
    keep_unknown: bool = True


class DataLifecycle(BaseModel):
    ttl_days: int = Field(default=3, ge=1)


class Database(BaseModel):
    path: str = "jobpulse.db"


class Logging(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file: str = "logs/jobpulse.log"
    max_bytes: int = Field(default=10_485_760, ge=1024)
    backup_count: int = Field(default=5, ge=0)


class Server(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)


class Cron(BaseModel):
    # When True, the app runs an in-process scheduler firing scrapes/cleanup
    # at the configured times. When False, scraping is triggered manually from
    # the UI (dev mode). Overridable via the JOBPULSE_CRON_ENABLED env var.
    enabled: bool = False


class Scrape(BaseModel):
    # Cap companies scraped per ATS per run (scraping every company is
    # impractical). None = no cap (scrape all). Bound it in production.
    max_companies_per_ats: int | None = Field(default=50, ge=1)
    # Per-ATS overrides of the cap above, e.g. {"workday": 5}. A Workday
    # "company" is a huge enterprise tenant (Accenture ≈ 60k postings), so
    # capping it low keeps scrapes fast. None as a value = no cap for that ATS.
    per_ats_overrides: dict[str, int | None] = Field(default_factory=dict)
    # Global thread budget across ALL ATS. ATS now run in parallel (each is a
    # different host with its own rate limit); this caps total concurrent
    # company fetches, distributed across ATS by live company count.
    concurrency: int = Field(default=20, ge=1, le=128)
    # Per-ATS politeness ceiling on concurrent company fetches. The distributor
    # never gives an ATS more workers than its ceiling here — key for hosts that
    # rate-limit hard (e.g. workable: its scraper spawns 4 internal threads per
    # company for .md detail fetches, so 2 here = 8 in-flight, the safe band).
    per_ats_concurrency: dict[str, int] = Field(default_factory=dict)
    default_ats_concurrency: int = Field(default=8, ge=1, le=64)
    # Skip companies that have proven to never post jobs in the target region.
    # A company is skipped only after it has been *reachable* (returned jobs)
    # for `skip_after_runs` runs in a row without one target-region job; skipped
    # companies are re-probed every `recheck_days` so newly in-region companies
    # are rediscovered. Companies that return nothing at all (a hiring lull or a
    # dead slug) are never skipped — we can't tell those apart from a foreign co.
    skip_unproductive: bool = True
    skip_after_runs: int = Field(default=3, ge=1)
    recheck_days: int = Field(default=30, ge=1)

    def cap_for(self, ats: str) -> int | None:
        """Company cap for an ATS — its override if set, else the global cap."""
        return self.per_ats_overrides.get(ats, self.max_companies_per_ats)

    def concurrency_for(self, ats: str) -> int:
        """Per-ATS concurrency ceiling — its override if set, else the default."""
        return self.per_ats_concurrency.get(ats, self.default_ats_concurrency)


class GoogleSearch(BaseModel):
    """Phase 2 Google-search discovery channel knobs."""

    # Hard cap on queries per run (rate_limiter enforces; overflow records a
    # 'partial' run rather than dropping silently). The evening slot can exceed
    # this — raise it, add schedule slots, or trim secondary-ATS cities.
    max_queries_per_run: int = Field(default=700, ge=1)
    # Randomized delay (seconds) between Google queries.
    min_delay: float = Field(default=5.0, ge=0)
    max_delay: float = Field(default=10.0, ge=0)
    # Abort a run after this many consecutive search failures.
    max_consecutive_failures: int = Field(default=5, ge=1)
    # search_results_cache TTL — skip re-fetching the same (query, url) within it.
    cache_ttl_hours: int = Field(default=24, ge=1)

    @field_validator("max_delay")
    @classmethod
    def _max_ge_min(cls, v: float, info) -> float:
        min_delay = info.data.get("min_delay", 0.0)
        if v < min_delay:
            raise ValueError("max_delay must be >= min_delay")
        return v


class AppConfig(BaseModel):
    target_roles: list[str] = Field(min_length=1)
    ats_platforms: ATSPlatforms
    schedule: Schedule = Schedule()
    location: Location = Location()
    data_lifecycle: DataLifecycle = DataLifecycle()
    database: Database = Database()
    logging: Logging = Logging()
    server: Server = Server()
    cron: Cron = Cron()
    scrape: Scrape = Scrape()
    google_search: GoogleSearch = GoogleSearch()

    @field_validator("target_roles")
    @classmethod
    def roles_not_empty_strings(cls, v: list[str]) -> list[str]:
        cleaned = [r.strip() for r in v if r.strip()]
        if not cleaned:
            raise ValueError("target_roles must contain at least one non-empty role")
        return cleaned


_CONFIG_ENV_VAR = "JOBPULSE_CONFIG"
_DEFAULT_CONFIG_PATH = "config.yaml"
_CRON_ENV_VAR = "JOBPULSE_CRON_ENABLED"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE_VALUES:
        return True
    if v in _FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean for {_CRON_ENV_VAR}: {value!r}")


def load_config(path: str | Path | None = None) -> AppConfig:
    # Note: .env is loaded by the entry points (app.create_app / scripts), not
    # here, so tests calling load_config() aren't affected by a developer's .env.
    if path is None:
        path = os.environ.get(_CONFIG_ENV_VAR, _DEFAULT_CONFIG_PATH)
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

    # Env override for the cron toggle takes precedence over config.yaml.
    cron_env = os.environ.get(_CRON_ENV_VAR)
    if cron_env is not None:
        raw.setdefault("cron", {})["enabled"] = _env_bool(cron_env)

    return AppConfig(**raw)
