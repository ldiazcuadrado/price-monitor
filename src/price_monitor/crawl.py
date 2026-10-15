"""Crawl one site: walk the listing pages, fetch detail pages, build validated products."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from price_monitor.config import SiteConfig
from price_monitor.fetch import Fetcher, FetchError
from price_monitor.models import Product, RawItem
from price_monitor.parse import ParseError, SiteParser, to_product

log = logging.getLogger(__name__)


class CrawlError(Exception):
    """The crawl could not produce a usable result."""


@dataclass(frozen=True, slots=True)
class CrawlResult:
    products: list[Product]
    failures: int
    pages: int


def _collect_listing(
    site: SiteConfig, fetcher: Fetcher, parser: SiteParser, limit_pages: int | None
) -> tuple[list[RawItem], int, int]:
    items: list[RawItem] = []
    failures = 0
    pages = 0
    for start_url in site.start_urls:
        url: str | None = start_url
        page = 0
        visited: set[str] = set()
        while url and url not in visited and (limit_pages is None or page < limit_pages):
            visited.add(url)
            page += 1
            try:
                listing = parser.parse_listing(fetcher.fetch(url), url, page)
            except (FetchError, ParseError) as exc:
                if page == 1 and not items:
                    raise CrawlError(f"cannot read the first listing page {url}: {exc}") from exc
                log.error(
                    "listing page failed; stopping pagination",
                    extra={"url": url, "error": str(exc)},
                )
                failures += 1
                break
            pages += 1
            failures += listing.skipped
            if not listing.items:
                break
            items.extend(listing.items)
            log.info(
                "listing page parsed",
                extra={
                    "site": site.name,
                    "page": page,
                    "items": len(listing.items),
                    "total": len(items),
                },
            )
            url = listing.next_url
    return items, failures, pages


def _fetch_details(
    site: SiteConfig, fetcher: Fetcher, parser: SiteParser, items: list[RawItem]
) -> tuple[list[RawItem], int]:
    def work(item: RawItem) -> RawItem | None:
        url = str(item["url"])
        try:
            extra = parser.parse_detail(fetcher.fetch(url), url, item)
        except (FetchError, ParseError) as exc:
            log.error("detail page skipped", extra={"url": url, "error": str(exc)})
            return None
        return {**item, **extra}

    with ThreadPoolExecutor(max_workers=site.request.concurrency) as pool:
        results = list(pool.map(work, items))
    merged = [r for r in results if r is not None]
    return merged, len(results) - len(merged)


def crawl_site(
    site: SiteConfig,
    fetcher: Fetcher,
    parser: SiteParser,
    *,
    limit_pages: int | None = None,
) -> CrawlResult:
    items, failures, pages = _collect_listing(site, fetcher, parser, limit_pages)
    if not items:
        raise CrawlError(f"no items found on {site.name!r}; has the page structure changed?")
    if site.detail is not None:
        items, detail_failures = _fetch_details(site, fetcher, parser, items)
        failures += detail_failures

    products: dict[str, Product] = {}
    for raw in items:
        try:
            product = to_product(site, raw)
        except ParseError as exc:
            failures += 1
            log.warning(
                "item rejected",
                extra={"site": site.name, "url": raw.get("url"), "reason": str(exc)},
            )
            continue
        if product.key in products:
            log.warning("duplicate key ignored", extra={"site": site.name, "key": product.key})
            continue
        products[product.key] = product
    if not products:
        raise CrawlError(f"no valid products on {site.name!r}; check the field selectors")
    log.info(
        "crawl finished",
        extra={"site": site.name, "products": len(products), "failures": failures, "pages": pages},
    )
    return CrawlResult(sorted(products.values(), key=lambda p: p.key), failures, pages)
