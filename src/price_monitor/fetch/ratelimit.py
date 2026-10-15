"""Per-domain rate limiting shared by all worker threads."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Spaces requests to the same host at least ``interval`` seconds apart.

    Slots are reserved under a lock and slept on outside it, so concurrent threads queue
    up in order instead of bursting.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._default = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}
        self._intervals: dict[str, float] = {}

    def interval_for(self, host: str) -> float:
        return self._intervals.get(host, self._default)

    def raise_interval(self, host: str, seconds: float) -> None:
        """Increase (never decrease) the interval for ``host``, e.g. for robots Crawl-delay."""
        with self._lock:
            self._intervals[host] = max(self._intervals.get(host, self._default), seconds)

    def wait(self, host: str) -> float:
        """Block until a request to ``host`` is allowed; return the time slept."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = slot + self.interval_for(host)
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)
