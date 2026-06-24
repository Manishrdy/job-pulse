"""Throttling and abort logic for bulk Google searches (Module M1-5).

The no-driver search engine is safe at manual volume; the real safety
mechanism for bulk runs is *pacing*. :class:`RateLimiter` enforces three
things across a run:

- a randomized delay between queries (default 5–10s) so requests don't look
  metronomic;
- a consecutive-failure counter that aborts the run once Google starts
  pushing back (default 5 in a row);
- a hard cap on queries per run, independent of how many were scheduled.

``sleep`` and ``rng`` are injected so tests are deterministic and instant.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class RunAbortedError(Exception):
    """Raised when the run should stop early (too many failures)."""


class RateLimiter:
    def __init__(
        self,
        *,
        min_delay: float = 5.0,
        max_delay: float = 10.0,
        max_consecutive_failures: int = 5,
        max_queries: int = 700,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if min_delay > max_delay:
            raise ValueError("min_delay must be <= max_delay")
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._max_consecutive_failures = max_consecutive_failures
        self._max_queries = max_queries
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._consecutive_failures = 0
        self._queries_run = 0

    @property
    def queries_run(self) -> int:
        return self._queries_run

    def budget_remaining(self) -> int:
        return max(0, self._max_queries - self._queries_run)

    def can_run(self) -> bool:
        """True while the per-run query budget isn't exhausted."""
        return self._queries_run < self._max_queries

    def before_query(self, *, first: bool = False) -> None:
        """Pace the next query and enforce the hard cap.

        Sleeps a randomized delay (skipped for the first query of a run), then
        counts the query. Raises :class:`RunAbortedError` if the budget is spent.
        """
        if not self.can_run():
            raise RunAbortedError(f"per-run query cap reached ({self._max_queries})")
        if not first:
            delay = self._rng.uniform(self._min_delay, self._max_delay)
            self._sleep(delay)
        self._queries_run += 1

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Count a failure; raise :class:`RunAbortedError` past the threshold."""
        self._consecutive_failures += 1
        log.warning(
            "Search failure %d/%d in a row",
            self._consecutive_failures,
            self._max_consecutive_failures,
        )
        if self._consecutive_failures >= self._max_consecutive_failures:
            raise RunAbortedError(
                f"{self._consecutive_failures} consecutive failures — aborting run"
            )
