"""FastAPI application factory.

Wires config, logging, the SQLite schema, static files, and the API
router together. Page routes (Module 5/6) are mounted here too once they
exist. Use :func:`create_app` so tests can inject a temporary config.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from jobpulse.config import AppConfig, load_config
from jobpulse.database import init_db
from jobpulse.logger import setup_logging
from jobpulse.routes.api import router as api_router
from jobpulse.routes.pages import router as pages_router
from jobpulse.scheduler import CronScheduler

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()
    setup_logging(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Ensure schema exists before serving requests.
        conn = init_db(config)
        conn.close()

        # Start the in-process scheduler only when the cron toggle is on.
        # When off, scraping is triggered manually from the UI (dev mode).
        scheduler: CronScheduler | None = None
        if config.cron.enabled:
            scheduler = CronScheduler(config)
            scheduler.start()
            log.info("JobPulse app started (cron ENABLED)")
        else:
            log.info("JobPulse app started (cron disabled — manual UI trigger)")

        app.state.scheduler = scheduler
        yield

        if scheduler is not None:
            scheduler.stop()
        log.info("JobPulse app stopped")

    app = FastAPI(title="JobPulse", lifespan=lifespan)
    app.state.config = config

    app.include_router(api_router)
    app.include_router(pages_router)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app
