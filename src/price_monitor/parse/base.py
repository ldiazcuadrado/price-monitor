"""Parser interface shared by the generic parser and site plugins."""

from __future__ import annotations

from typing import Protocol

from price_monitor.models import ListingPage, RawItem


class ParseError(Exception):
    """A page or item could not be turned into data."""


class SiteParser(Protocol):
    """Turns fetched HTML into raw items.

    Implementations return plain dicts keyed by field name. A ``url`` key (absolute) is
    required whenever detail pages are fetched. Core field names are ``title``, ``price``,
    ``category``, ``stock`` and ``in_stock``; any other key becomes a product attribute.
    """

    def parse_listing(self, html: str, url: str, page: int) -> ListingPage:
        """Parse listing page number ``page`` (1-based) fetched from ``url``."""
        ...

    def parse_detail(self, html: str, url: str, item: RawItem) -> RawItem:
        """Return the extra fields found on a detail page; they override ``item``."""
        ...
