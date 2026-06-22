from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class ATSPlatforms(BaseModel):
    primary: list[str] = Field(min_length=1)
    secondary: list[str] = []
    low_priority: list[str] = []

    @property
    def all_platforms(self) -> list[str]:
        return self.primary + self.secondary + self.low_priority


class Schedule(BaseModel):
    morning: str = "08:00"
    afternoon: str = "13:00"
    evening: str = "18:00"
    cleanup: str = "02:00"
    timezone: str = "US/Pacific"


class Location(BaseModel):
    primary: str = "United States"
    remote_preferred: bool = True


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
    # Concurrent company fetches within a single ATS. Bounded for politeness
    # (one ATS is scraped at a time, so this is per-provider parallelism).
    concurrency: int = Field(default=8, ge=1, le=64)


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
    # Load .env (if present) so env overrides like JOBPULSE_CRON_ENABLED apply.
    load_dotenv()

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
