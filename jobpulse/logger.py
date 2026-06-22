from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jobpulse.config import AppConfig

_INITIALIZED = False


def setup_logging(config: AppConfig) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    log_path = Path(config.logging.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(config.logging.level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def reset_logging() -> None:
    """Remove all handlers from root logger. Used in tests."""
    global _INITIALIZED
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
    _INITIALIZED = False
