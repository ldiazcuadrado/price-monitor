from __future__ import annotations

import random
import sys

import httpx
import pytest
import respx

from price_monitor.config import SiteConfig
from price_monitor.fetch import FetchError, RobotsDisallowedError, build_fetcher
from price_monitor.fetch.base import Response, TransportError
from price_monitor.fetch.http import HttpxTransport
from price_monitor.fetch.polite import PoliteFetcher, backoff_delay
from price_monitor.fetch.ratelimit import RateLimiter
from price_monitor.fetch.robots import RobotsChecker
from price_monitor.settings import Settings

UA = "price-monitor/test"
ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 3

User-agent: badbot
Disallow: /
"""


class ScriptedTransport:
    """Replays queued responses or errors, recording the requested URLs."""

    def __init__(self, *script: Response | TransportError) -> None:
        self.script = list(script)
        self.urls: list[str] = []

    def get(self, url: str) -> Response:
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, TransportError):
            raise item
        return item

    def close(self) -> None:
        pass


def ok(text: str = "<html/>") -> Response:
    return Response(200, text)


def fetcher(
    transport: ScriptedTransport,
    robots: RobotsChecker | None = None,
    retries: int = 3,
    sleeps: list[float] | None = None,
) -> PoliteFetcher:
    log = sleeps if sleeps is not None else []
    return PoliteFetcher(
        transport,
        RateLimiter(0.0),
        robots=robots,
        max_retries=retries,
        backoff_base=1.0,
        sleep=log.append,
        rng=random.Random(1),
    )


def test_robots_rules_and_crawl_delay() -> None:
    checker = RobotsChecker(ScriptedTransport(ok(ROBOTS)), UA)
    assert checker.allowed("https://x.test/products")
    assert not checker.allowed("https://x.test/private/page")
    assert checker.crawl_delay("https://x.test/products") == 3.0


def test_robots_is_cached_per_origin() -> None:
    transport = ScriptedTransport(ok(ROBOTS), ok(""))
    checker = RobotsChecker(transport, UA)
    checker.allowed("https://x.test/a")
    checker.allowed("https://x.test/b")
    checker.allowed("https://y.test/a")
    assert transport.urls == ["https://x.test/robots.txt", "https://y.test/robots.txt"]


def test_specific_user_agent_blocks() -> None:
    checker = RobotsChecker(ScriptedTransport(ok(ROBOTS)), "badbot/1.0")
    assert not checker.allowed("https://x.test/anything")


@pytest.mark.parametrize(
    ("response", "allowed"),
    [
        (Response(404, ""), True),
        (Response(410, ""), True),
        (Response(403, ""), False),
        (Response(500, ""), False),
        (TransportError("down"), False),
    ],
)
def test_robots_failure_modes(response: Response | TransportError, allowed: bool) -> None:
    checker = RobotsChecker(ScriptedTransport(response), UA)
    assert checker.allowed("https://x.test/a") is allowed
    assert checker.crawl_delay("https://x.test/a") is None


def test_fetch_blocked_by_robots_makes_no_page_request() -> None:
    transport = ScriptedTransport(ok(ROBOTS))
    robots = RobotsChecker(transport, UA)
    with pytest.raises(RobotsDisallowedError):
        fetcher(ScriptedTransport(), robots).fetch("https://x.test/private/a")


def test_fetch_applies_crawl_delay_to_limiter() -> None:
    robots = RobotsChecker(ScriptedTransport(ok(ROBOTS)), UA)
    page_transport = ScriptedTransport(ok("page"))
    limiter = RateLimiter(0.0)
    polite = PoliteFetcher(page_transport, limiter, robots=robots, sleep=lambda _: None)
    assert polite.fetch("https://x.test/products") == "page"
    assert limiter.interval_for("x.test") == 3.0


def test_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    transport = ScriptedTransport(Response(503, ""), TransportError("reset"), ok("done"))
    assert fetcher(transport, sleeps=sleeps).fetch("https://x.test/a") == "done"
    assert len(transport.urls) == 3
    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.0
    assert 1.0 <= sleeps[1] <= 2.0


def test_gives_up_after_retries() -> None:
    transport = ScriptedTransport(*[Response(500, "")] * 3)
    with pytest.raises(FetchError, match="giving up after 3 attempts"):
        fetcher(transport, retries=2).fetch("https://x.test/a")


def test_client_errors_are_not_retried() -> None:
    transport = ScriptedTransport(Response(404, ""))
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher(transport).fetch("https://x.test/a")
    assert len(transport.urls) == 1


def test_retry_after_header_is_honoured_and_capped() -> None:
    sleeps: list[float] = []
    transport = ScriptedTransport(
        Response(429, "", {"Retry-After": "7"}),
        Response(429, "", {"retry-after": "9999"}),
        Response(429, "", {"Retry-After": "soon"}),
        ok(),
    )
    fetcher(transport, sleeps=sleeps).fetch("https://x.test/a")
    assert sleeps[0] == 7.0
    assert sleeps[1] == 60.0
    assert sleeps[2] <= 4.0


def test_backoff_grows_and_is_capped() -> None:
    rng = random.Random(0)
    assert backoff_delay(0, 1.0, rng) <= 1.0
    assert backoff_delay(3, 1.0, rng) >= 4.0
    assert backoff_delay(20, 1.0, rng, cap=30.0) <= 30.0


@respx.mock
def test_httpx_transport_sends_headers_and_encoding() -> None:
    route = respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, content="£5".encode())
    )
    transport = HttpxTransport(user_agent="ua/1", headers={"X-Test": "1"}, encoding="utf-8")
    response = transport.get("https://x.test/a")
    transport.close()
    assert response.text == "£5"
    assert route.calls.last.request.headers["user-agent"] == "ua/1"
    assert route.calls.last.request.headers["x-test"] == "1"


@respx.mock
def test_httpx_transport_wraps_network_errors() -> None:
    respx.get("https://x.test/a").mock(side_effect=httpx.ConnectTimeout("slow"))
    transport = HttpxTransport(user_agent="ua")
    with pytest.raises(TransportError, match="ConnectTimeout"):
        transport.get("https://x.test/a")
    transport.close()


@respx.mock
def test_build_fetcher_end_to_end(books_site: SiteConfig, settings: Settings) -> None:
    respx.get("https://books.test/robots.txt").respond(404)
    respx.get("https://books.test/page").respond(200, text="hello")
    built = build_fetcher(books_site, settings)
    assert built.fetch("https://books.test/page") == "hello"
    built.close()


@respx.mock
def test_build_fetcher_without_robots_skips_the_check(
    books_site: SiteConfig, settings: Settings
) -> None:
    robots = respx.get("https://books.test/robots.txt").respond(403)
    respx.get("https://books.test/page").respond(200, text="hello")
    built = build_fetcher(books_site, settings, respect_robots=False)
    assert built.fetch("https://books.test/page") == "hello"
    assert not robots.called
    built.close()


def test_browser_renderer_without_playwright_explains_how_to_install(
    books_site: SiteConfig, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    browser_site = books_site.model_copy(update={"renderer": "browser"})
    with pytest.raises(FetchError, match=r"price-monitor\[browser\]"):
        build_fetcher(browser_site, settings)
