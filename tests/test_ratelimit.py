from __future__ import annotations

import threading

from price_monitor.fetch.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds


def make(interval: float) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return RateLimiter(interval, clock=clock.time, sleep=clock.sleep), clock


def test_first_request_is_immediate_then_spaced() -> None:
    limiter, clock = make(2.0)
    assert limiter.wait("a.test") == 0
    assert limiter.wait("a.test") == 2.0
    assert limiter.wait("a.test") == 2.0
    assert clock.sleeps == [2.0, 2.0]


def test_domains_are_independent() -> None:
    limiter, clock = make(5.0)
    limiter.wait("a.test")
    assert limiter.wait("b.test") == 0
    assert clock.sleeps == []


def test_elapsed_time_counts() -> None:
    limiter, clock = make(3.0)
    limiter.wait("a.test")
    clock.now += 1.0
    assert limiter.wait("a.test") == 2.0
    clock.now += 10.0
    assert limiter.wait("a.test") == 0


def test_interval_can_only_be_raised() -> None:
    limiter, _ = make(1.0)
    limiter.raise_interval("a.test", 4.0)
    limiter.raise_interval("a.test", 2.0)
    assert limiter.interval_for("a.test") == 4.0
    assert limiter.interval_for("b.test") == 1.0


def test_concurrent_waiters_get_distinct_slots() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(1.0, clock=lambda: 50.0, sleep=sleeps.append)
    results: list[float] = []
    threads = [
        threading.Thread(target=lambda: results.append(limiter.wait("a.test"))) for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert sorted(sleeps) == [1.0, 2.0, 3.0, 4.0]
