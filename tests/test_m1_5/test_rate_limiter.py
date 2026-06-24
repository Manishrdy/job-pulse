"""M1-5 — rate limiter: pacing, failure abort, per-run cap."""

from __future__ import annotations

import random

import pytest

from jobpulse.google_search.rate_limiter import RateLimiter, RunAbortedError


def _limiter(**kw):
    """A limiter with a recording sleep and a seeded RNG."""
    slept: list[float] = []
    kw.setdefault("sleep", slept.append)
    kw.setdefault("rng", random.Random(0))
    return RateLimiter(**kw), slept


def test_first_query_does_not_sleep():
    rl, slept = _limiter()
    rl.before_query(first=True)
    assert slept == []
    assert rl.queries_run == 1


def test_subsequent_queries_sleep_within_bounds():
    rl, slept = _limiter(min_delay=5.0, max_delay=10.0)
    rl.before_query(first=True)
    for _ in range(5):
        rl.before_query()
    assert len(slept) == 5
    assert all(5.0 <= d <= 10.0 for d in slept)


def test_record_failure_aborts_after_threshold():
    rl, _ = _limiter(max_consecutive_failures=3)
    rl.record_failure()
    rl.record_failure()
    with pytest.raises(RunAbortedError):
        rl.record_failure()


def test_success_resets_failure_streak():
    rl, _ = _limiter(max_consecutive_failures=3)
    rl.record_failure()
    rl.record_failure()
    rl.record_success()
    # Streak reset — two more failures should not abort.
    rl.record_failure()
    rl.record_failure()
    # The third in the new streak aborts.
    with pytest.raises(RunAbortedError):
        rl.record_failure()


def test_per_run_cap_enforced():
    rl, _ = _limiter(max_queries=3)
    rl.before_query(first=True)
    rl.before_query()
    rl.before_query()
    assert not rl.can_run()
    with pytest.raises(RunAbortedError):
        rl.before_query()


def test_budget_remaining():
    rl, _ = _limiter(max_queries=5)
    assert rl.budget_remaining() == 5
    rl.before_query(first=True)
    assert rl.budget_remaining() == 4


def test_invalid_delay_bounds():
    with pytest.raises(ValueError):
        RateLimiter(min_delay=10.0, max_delay=5.0)
