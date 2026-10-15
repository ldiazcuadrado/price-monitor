"""Parser driven entirely by the field selectors in a site's configuration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from price_monitor.config import (
    FieldSpec,
    NextLinkPagination,
    SiteConfig,
    UrlTemplatePagination,
)
from price_monitor.models import ListingPage, RawItem
from price_monitor.parse.base import ParseError
from price_monitor.transforms import TransformError, apply_transforms

log = logging.getLogger(__name__)


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _raw_value(element: Tag, spec: FieldSpec) -> str | None:
    target: Tag | None = element
    if spec.css:
        target = element.select_one(spec.css)
    if target is None:
        return None
    if spec.attr:
        value = target.get(spec.attr)
        if value is None:
            return None
        return " ".join(value) if isinstance(value, list) else str(value)
    return " ".join(target.get_text().split())


def extract_field(element: Tag, name: str, spec: FieldSpec) -> Any:
    """Return the field value, or raise ParseError when it is absent and has no default."""
    raw = _raw_value(element, spec)
    if raw is None:
        if spec.has_default:
            return spec.default
        raise ParseError(f"field {name!r}: nothing matched {spec.css or '<item>'!r}")
    try:
        return apply_transforms(raw, spec.transforms)
    except TransformError as exc:
        if spec.has_default:
            return spec.default
        raise ParseError(f"field {name!r}: {exc}") from exc


def _extract_all(element: Tag, specs: dict[str, FieldSpec], base_url: str) -> RawItem:
    item: RawItem = {}
    for name, spec in specs.items():
        item[name] = extract_field(element, name, spec)
    if "url" in item:
        item["url"] = urljoin(base_url, str(item["url"]))
    return item


class GenericParser:
    def __init__(self, site: SiteConfig) -> None:
        self._site = site

    def parse_listing(self, html: str, url: str, page: int) -> ListingPage:
        soup = make_soup(html)
        items: list[RawItem] = []
        skipped = 0
        selector = self._site.item_selector or ""
        for element in soup.select(selector):
            try:
                item = _extract_all(element, self._site.fields, url)
            except ParseError as exc:
                skipped += 1
                log.warning("listing item skipped", extra={"url": url, "reason": str(exc)})
                continue
            item.setdefault("url", url)
            items.append(item)
        return ListingPage(items=items, next_url=self._next_url(soup, url, page), skipped=skipped)

    def parse_detail(self, html: str, url: str, item: RawItem) -> RawItem:
        if self._site.detail is None:
            return {}
        soup = make_soup(html)
        extra: RawItem = {}
        for name, spec in self._site.detail.fields.items():
            extra[name] = extract_field(soup, name, spec)
        return extra

    def _next_url(self, soup: BeautifulSoup, url: str, page: int) -> str | None:
        rule = self._site.pagination
        if rule is None or page >= rule.max_pages:
            return None
        if isinstance(rule, NextLinkPagination):
            link = soup.select_one(rule.selector)
            href = link.get("href") if link else None
            return urljoin(url, str(href)) if href else None
        if isinstance(rule, UrlTemplatePagination):
            return rule.template.format(page=rule.first_page + page)
        return None
