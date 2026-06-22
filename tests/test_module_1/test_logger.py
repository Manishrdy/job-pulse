from __future__ import annotations

import logging
from pathlib import Path

from jobpulse.config import AppConfig
from jobpulse.logger import reset_logging, setup_logging


def test_logger_creates_log_file(test_config: AppConfig):
    reset_logging()
    setup_logging(test_config)
    log = logging.getLogger("test_logger")
    log.info("Test log message")

    log_path = Path(test_config.logging.file)
    assert log_path.exists()
    content = log_path.read_text()
    assert "Test log message" in content
    reset_logging()


def test_logger_format(test_config: AppConfig):
    reset_logging()
    setup_logging(test_config)
    log = logging.getLogger("test_format")
    log.info("Formatted message")

    log_path = Path(test_config.logging.file)
    content = log_path.read_text()
    assert "INFO" in content
    assert "test_format" in content
    assert "Formatted message" in content
    reset_logging()


def test_logger_creates_parent_dirs(tmp_path: Path):
    from jobpulse.config import AppConfig

    config = AppConfig(
        target_roles=["Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        database={"path": str(tmp_path / "test.db")},
        logging={
            "level": "DEBUG",
            "file": str(tmp_path / "deep" / "nested" / "test.log"),
            "max_bytes": 10_485_760,
            "backup_count": 5,
        },
    )
    reset_logging()
    setup_logging(config)
    log = logging.getLogger("test_nested")
    log.info("Nested dir test")

    assert (tmp_path / "deep" / "nested" / "test.log").exists()
    reset_logging()


def test_logger_rotation_config(test_config: AppConfig):
    from logging.handlers import RotatingFileHandler

    reset_logging()
    setup_logging(test_config)

    root = logging.getLogger()
    rotating_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating_handlers) == 1
    handler = rotating_handlers[0]
    assert handler.maxBytes == test_config.logging.max_bytes
    assert handler.backupCount == test_config.logging.backup_count
    reset_logging()


def test_logger_idempotent(test_config: AppConfig):
    reset_logging()
    setup_logging(test_config)
    setup_logging(test_config)

    root = logging.getLogger()
    from logging.handlers import RotatingFileHandler
    rotating_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating_handlers) == 1
    reset_logging()
