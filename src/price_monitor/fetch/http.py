"""Plain HTTP transport built on httpx."""

from __future__ import annotations

import httpx

from price_monitor.fetch.base import Response, TransportError


class HttpxTransport:
    def __init__(
        self,
        *,
        user_agent: str,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        encoding: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        merged = {"User-Agent": user_agent, **(headers or {})}
        self._client = client or httpx.Client(
            headers=merged, timeout=timeout, follow_redirects=True
        )
        self._encoding = encoding

    def get(self, url: str) -> Response:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        if self._encoding:
            response.encoding = self._encoding
        return Response(response.status_code, response.text, dict(response.headers))

    def close(self) -> None:
        self._client.close()
