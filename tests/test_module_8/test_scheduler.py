from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from jobpulse.config import AppConfig
from jobpulse.scheduler import CronScheduler

TZ = ZoneInfo("US/Pacific")


def _config() -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        schedule={
            "morning": "08:00",
            "afternoon": "13:00",
            "evening": "18:00",
            "cleanup": "02:00",
            "timezone": "US/Pacific",
        },
    )


def _make() -> tuple[CronScheduler, list]:
    fired: list = []
    sched = CronScheduler(
        _config(),
        scrape_runner=lambda slot: fired.append(("scrape", slot)),
        cleanup_runner=lambda: fired.append(("cleanup", None)),
    )
    return sched, fired


def test_due_slots_matches_morning():
    sched, _ = _make()
    now = datetime(2026, 6, 22, 8, 0, tzinfo=TZ)
    assert sched.due_slots(now) == ["morning"]


def test_due_slots_none_offpeak():
    sched, _ = _make()
    now = datetime(2026, 6, 22, 9, 17, tzinfo=TZ)
    assert sched.due_slots(now) == []


def test_tick_fires_scrape():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 13, 0, tzinfo=TZ)
    assert sched.tick(now) == ["afternoon"]
    assert ("scrape", "afternoon") in fired


def test_tick_fires_cleanup():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 2, 0, tzinfo=TZ)
    assert sched.tick(now) == ["cleanup"]
    assert ("cleanup", None) in fired


def test_slot_fires_once_per_day():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 8, 0, tzinfo=TZ)
    assert sched.tick(now) == ["morning"]
    # same minute again → already fired today, no refire
    assert sched.tick(now) == []
    assert len(fired) == 1


def test_slot_refires_next_day():
    sched, fired = _make()
    day1 = datetime(2026, 6, 22, 8, 0, tzinfo=TZ)
    day2 = datetime(2026, 6, 23, 8, 0, tzinfo=TZ)
    sched.tick(day1)
    assert sched.tick(day2) == ["morning"]
    assert len(fired) == 2
