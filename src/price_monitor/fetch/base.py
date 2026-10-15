"""Fetching interfaces and errors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


class FetchError(Exception):
    """A URL could not be fetched."""


class RobotsDisallowedError(FetchError):
    """robots.txt forbids fetching the URL."""


class TransportError(Exception):
    """Network-level failure (timeout, connection error); worth retrying."""


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    text: str
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    """Performs one GET. Returns any HTTP status; raises TransportError on network failure."""

    def get(self, url: str) -> Response: ...

    def close(self) -> None: ...


class Fetcher(Protocol):
    """Returns the HTML of a URL, applying robots, rate limiting and retries."""

    def fetch(self, url: str) -> str: ...

    def close(self) -> None: ...
