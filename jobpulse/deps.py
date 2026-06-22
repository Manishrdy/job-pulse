"""FastAPI dependencies: per-request DB connection and config access.

Kept in its own module so both ``app.py`` and ``routes/*`` can import the
dependencies without a circular import.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from jobpulse.config import AppConfig
from jobpulse.database import get_connection


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Open a fresh SQLite connection per request (WAL allows concurrency)."""
    conn = get_connection(request.app.state.config.database.path)
    try:
        yield conn
    finally:
        conn.close()
