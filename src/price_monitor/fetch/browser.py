"""Optional JavaScript-rendering transport using Playwright (the ``browser`` extra)."""

from __future__ import annotations

from typing import Any

from price_monitor.config import BrowserConfig
from price_monitor.fetch.base import FetchError, Response, TransportError

_INSTALL_HINT = (
    "the 'browser' renderer needs Playwright: install with `pip install price-monitor[browser]` "
    "and run `playwright install chromium`"
)


class PlaywrightTransport:
    def __init__(
        self,
        *,
        user_agent: str,
        headers: dict[str, str],
        timeout: float,
        options: BrowserConfig,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(_INSTALL_HINT) from exc
        self._options = options
        self._timeout_ms = timeout * 1000
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        self._context: Any = self._browser.new_context(
            user_agent=user_agent, extra_http_headers=headers
        )

    def get(self, url: str) -> Response:
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until=self._options.wait_until, timeout=self._timeout_ms)
            if self._options.wait_for_selector:
                page.wait_for_selector(self._options.wait_for_selector, timeout=self._timeout_ms)
            status = response.status if response else 0
            return Response(status, page.content())
        except Exception as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            page.close()

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()
