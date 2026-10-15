"""Assembly of the fetcher for a site."""

from __future__ import annotations

from price_monitor.config import SiteConfig
from price_monitor.fetch.base import Fetcher, Transport
from price_monitor.fetch.http import HttpxTransport
from price_monitor.fetch.polite import PoliteFetcher
from price_monitor.fetch.ratelimit import RateLimiter
from price_monitor.fetch.robots import RobotsChecker
from price_monitor.settings import Settings


def build_fetcher(site: SiteConfig, settings: Settings, *, respect_robots: bool = True) -> Fetcher:
    request = site.request
    user_agent = request.user_agent or settings.user_agent
    http = HttpxTransport(
        user_agent=user_agent,
        headers=request.headers,
        timeout=request.timeout_seconds,
        encoding=request.encoding,
    )
    transport: Transport = http
    if site.renderer == "browser":
        from price_monitor.fetch.browser import PlaywrightTransport

        transport = PlaywrightTransport(
            user_agent=user_agent,
            headers=request.headers,
            timeout=request.timeout_seconds,
            options=site.browser,
        )
    robots = RobotsChecker(http, user_agent) if respect_robots else None
    return PoliteFetcher(
        transport,
        RateLimiter(request.delay_seconds),
        robots=robots,
        max_retries=request.max_retries,
        backoff_base=request.backoff_base_seconds,
        closers=[http.close] if transport is not http else [],
    )
