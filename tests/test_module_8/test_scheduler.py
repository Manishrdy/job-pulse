from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from jobpulse.config import AppConfig
from jobpulse.scheduler import CronScheduler

TZ = ZoneInfo("America/New_York")


def _config(scrape_times=None, cleanup_time="02:00") -> AppConfig:
    return AppConfig(
        target_roles=["Software Engineer"],
        ats_platforms={"primary": ["greenhouse"]},
        schedule={
            "scrape_times": scrape_times or ["05:00"],
            "cleanup_time": cleanup_time,
            "timezone": "America/New_York",
        },
    )


def _make(scrape_times=None) -> tuple[CronScheduler, list]:
    fired: list = []
    sched = CronScheduler(
        _config(scrape_times),
        scrape_runner=lambda slot: fired.append(("scrape", slot)),
        cleanup_runner=lambda: fired.append(("cleanup", None)),
    )
    return sched, fired


def test_due_slots_matches_daily_scrape():
    sched, _ = _make()
    now = datetime(2026, 6, 22, 5, 0, tzinfo=TZ)
    assert sched.due_slots(now) == ["scrape@05:00"]


def test_due_slots_none_offpeak():
    sched, _ = _make()
    now = datetime(2026, 6, 22, 9, 17, tzinfo=TZ)
    assert sched.due_slots(now) == []


def test_tick_fires_daily_scrape():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 5, 0, tzinfo=TZ)
    assert sched.tick(now) == ["scrape@05:00"]
    assert ("scrape", "scrape@05:00") in fired


def test_tick_fires_cleanup():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 2, 0, tzinfo=TZ)
    assert sched.tick(now) == ["cleanup@02:00"]
    assert ("cleanup", None) in fired


def test_slot_fires_once_per_day():
    sched, fired = _make()
    now = datetime(2026, 6, 22, 5, 0, tzinfo=TZ)
    assert sched.tick(now) == ["scrape@05:00"]
    # same minute again → already fired today, no refire
    assert sched.tick(now) == []
    assert len(fired) == 1


def test_slot_refires_next_day():
    sched, fired = _make()
    day1 = datetime(2026, 6, 22, 5, 0, tzinfo=TZ)
    day2 = datetime(2026, 6, 23, 5, 0, tzinfo=TZ)
    sched.tick(day1)
    assert sched.tick(day2) == ["scrape@05:00"]
    assert len(fired) == 2


def test_multiple_scrape_times_supported():
    # Adding more times yields more daily slots (flexibility for the future).
    sched, fired = _make(scrape_times=["05:00", "17:00"])
    morning = datetime(2026, 6, 22, 5, 0, tzinfo=TZ)
    evening = datetime(2026, 6, 22, 17, 0, tzinfo=TZ)
    assert sched.tick(morning) == ["scrape@05:00"]
    assert sched.tick(evening) == ["scrape@17:00"]
    assert len(fired) == 2
