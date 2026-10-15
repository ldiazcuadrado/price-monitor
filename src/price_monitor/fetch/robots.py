"""robots.txt evaluation with per-origin caching."""

from __future__ import annotations

import logging
import threading
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from price_monitor.fetch.base import Transport, TransportError

log = logging.getLogger(__name__)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class RobotsChecker:
    """Answers whether a URL may be fetched, following RFC 9309 status handling.

    404 and other 4xx mean there are no rules (allow). A 5xx response or a network error
    is treated as "disallow all" for the origin, the conservative reading of the RFC.
    """

    def __init__(self, transport: Transport, user_agent: str) -> None:
        self._transport = transport
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | bool] = {}
        self._lock = threading.Lock()

    def _load(self, origin: str) -> RobotFileParser | bool:
        robots_url = f"{origin}/robots.txt"
        try:
            response = self._transport.get(robots_url)
        except TransportError as exc:
            log.warning(
                "robots.txt unreachable; assuming disallow",
                extra={"url": robots_url, "error": str(exc)},
            )
            return False
        if response.status == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            return parser
        if response.status >= 500:
            log.warning(
                "robots.txt server error; assuming disallow",
                extra={"url": robots_url, "status": response.status},
            )
            return False
        if response.status in (401, 403):
            log.warning(
                "robots.txt access denied; assuming disallow",
                extra={"url": robots_url, "status": response.status},
            )
            return False
        return True

    def _rules(self, url: str) -> RobotFileParser | bool:
        origin = _origin(url)
        with self._lock:
            if origin not in self._parsers:
                self._parsers[origin] = self._load(origin)
            return self._parsers[origin]

    def allowed(self, url: str) -> bool:
        rules = self._rules(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        rules = self._rules(url)
        if isinstance(rules, bool):
            return None
        delay = rules.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None
