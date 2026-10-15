"""Example site plugin for pages the CSS configuration cannot express.

Reference it from sites.yaml with ``plugin: custom_parser:JsonLdParser`` and make the module
importable (for example ``PYTHONPATH=examples``). The class receives the ``SiteConfig`` and
must provide ``parse_listing`` and ``parse_detail``.
"""

from __future__ import annotations

import json
import re

from price_monitor.config import SiteConfig
from price_monitor.models import ListingPage, RawItem

_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


class JsonLdParser:
    """Reads schema.org Product data embedded in the page instead of using selectors."""

    def __init__(self, site: SiteConfig) -> None:
        self._site = site

    def parse_listing(self, html: str, url: str, page: int) -> ListingPage:
        items: list[RawItem] = []
        for block in _JSON_LD.findall(html):
            data = json.loads(block)
            if data.get("@type") != "Product":
                continue
            offer = data.get("offers", {})
            items.append(
                {
                    "title": data["name"],
                    "price": float(offer["price"]),
                    "sku": data.get("sku", data["name"]),
                    "url": data.get("url", url),
                    "in_stock": offer.get("availability", "").endswith("InStock"),
                }
            )
        return ListingPage(items=items)

    def parse_detail(self, html: str, url: str, item: RawItem) -> RawItem:
        return {}
