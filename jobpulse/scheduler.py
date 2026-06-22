"""In-process cron scheduler (Module 8).

When ``cron.enabled`` is true, the app runs a background daemon thread that
fires the scrape pipeline at the morning/afternoon/evening times and the
cleanup pipeline at the nightly time — all from ``config.schedule`` in the
configured timezone (FR-01.3 / §4.3). When disabled, this never starts and
scraping is driven manually from the UI instead.

The matcher is intentionally minute-resolution and dependency-free: a tick
every ``CHECK_INTERVAL`` seconds compares the current local ``HH:MM`` to
each slot, firing once per slot per day (tracked in ``_last_fired``). This
avoids pulling in a heavyweight scheduler while still honoring the toggle
and timezone requirements.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobpulse.config import AppConfig
from jobpulse.pipeline import run_cleanup_pipeline, run_scrape_pipeline

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30


class CronScheduler:
    """Minute-resolution scheduler firing pipeline runs at configured times."""

    def __init__(
        self,
        config: AppConfig,
        *,
        scrape_runner: Callable[..., object] | None = None,
        cleanup_runner: Callable[..., object] | None = None,
    ) -> None:
        self.config = config
        self.tz = ZoneInfo(config.schedule.timezone)
        self._scrape = scrape_runner or self._default_scrape
        self._cleanup = cleanup_runner or self._default_cleanup
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fired: dict[str, date] = {}

        # slot name -> ("HH:MM", action)
        s = config.schedule
        self._slots: dict[str, tuple[str, str]] = {
            "morning": (s.morning, "scrape"),
            "afternoon": (s.afternoon, "scrape"),
            "evening": (s.evening, "scrape"),
            "cleanup": (s.cleanup, "cleanup"),
        }

    def _default_scrape(self, slot: str) -> None:
        run_scrape_pipeline(self.config, schedule_slot=slot)

    def _default_cleanup(self) -> None:
        run_cleanup_pipeline(self.config)

    def due_slots(self, now_local: datetime) -> list[str]:
        """Slots whose configured time matches ``now_local`` and not yet fired today."""
        current = now_local.strftime("%H:%M")
        due = []
        for slot, (slot_time, _action) in self._slots.items():
            if slot_time == current and self._last_fired.get(slot) != now_local.date():
                due.append(slot)
        return due

    def tick(self, now_local: datetime | None = None) -> list[str]:
        """Fire any due slots once. Returns the slots fired (for testing)."""
        now_local = now_local or datetime.now(self.tz)
        fired = []
        for slot in self.due_slots(now_local):
            self._last_fired[slot] = now_local.date()
            _time, action = self._slots[slot]
            log.info("Scheduler firing slot %r (%s)", slot, action)
            try:
                if action == "scrape":
                    self._scrape(slot)
                else:
                    self._cleanup()
            except Exception:
                log.exception("Scheduled %s run failed", action)
            fired.append(slot)
        return fired

    def _loop(self) -> None:
        log.info("Cron scheduler started (tz=%s)", self.config.schedule.timezone)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Scheduler tick error")
            self._stop.wait(CHECK_INTERVAL_SECONDS)
        log.info("Cron scheduler stopped")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jobpulse-cron")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
