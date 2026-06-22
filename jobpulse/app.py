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
        log.info("JobPulse app started")
        yield
        log.info("JobPulse app stopped")

    app = FastAPI(title="JobPulse", lifespan=lifespan)
    app.state.config = config

    app.include_router(api_router)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app
