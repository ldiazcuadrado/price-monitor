"""The fetcher used by the crawler: robots.txt, rate limiting, retries with backoff."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from price_monitor.fetch.base import (
    FetchError,
    Response,
    RobotsDisallowedError,
    Transport,
    TransportError,
)
from price_monitor.fetch.ratelimit import RateLimiter
from price_monitor.fetch.robots import RobotsChecker

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0


def backoff_delay(attempt: int, base: float, rng: random.Random, cap: float = 60.0) -> float:
    """Exponential backoff with equal jitter: half fixed, half random."""
    ceiling = min(cap, base * 2.0**attempt)
    return ceiling / 2 + rng.uniform(0, ceiling / 2)


def _retry_after(response: Response) -> float | None:
    value = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), None)
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER)
    except ValueError:
        return None


class PoliteFetcher:
    def __init__(
        self,
        transport: Transport,
        limiter: RateLimiter,
        *,
        robots: RobotsChecker | None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        closers: Sequence[Callable[[], None]] = (),
    ) -> None:
        self._transport = transport
        self._limiter = limiter
        self._robots = robots
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._closers = closers

    def fetch(self, url: str) -> str:
        host = urlsplit(url).netloc
        if self._robots is not None:
            if not self._robots.allowed(url):
                raise RobotsDisallowedError(f"robots.txt disallows {url}")
            crawl_delay = self._robots.crawl_delay(url)
            if crawl_delay:
                self._limiter.raise_interval(host, crawl_delay)

        last_problem = "no attempt made"
        for attempt in range(self._max_retries + 1):
            self._limiter.wait(host)
            retry_after: float | None = None
            try:
                response = self._transport.get(url)
            except TransportError as exc:
                last_problem = str(exc)
            else:
                if response.status < 400:
                    return response.text
                last_problem = f"HTTP {response.status}"
                if response.status not in RETRY_STATUSES:
                    raise FetchError(f"{url}: {last_problem}")
                retry_after = _retry_after(response)
            if attempt < self._max_retries:
                delay = retry_after
                if delay is None:
                    delay = backoff_delay(attempt, self._backoff_base, self._rng)
                log.warning(
                    "request failed; retrying",
                    extra={
                        "url": url,
                        "problem": last_problem,
                        "attempt": attempt + 1,
                        "delay": round(delay, 2),
                    },
                )
                self._sleep(delay)
        raise FetchError(
            f"{url}: giving up after {self._max_retries + 1} attempts ({last_problem})"
        )

    def close(self) -> None:
        self._transport.close()
        for close in self._closers:
            close()
