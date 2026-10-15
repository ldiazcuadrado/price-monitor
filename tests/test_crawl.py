from __future__ import annotations

import pytest
import respx

from price_monitor.config import SiteConfig
from price_monitor.crawl import CrawlError, CrawlResult, crawl_site
from price_monitor.fetch import build_fetcher
from price_monitor.parse import GenericParser
from price_monitor.settings import Settings
from tests.helpers import Book, FakeShop

PAGE_1 = [Book("u1", "Alpha", 10.0), Book("u2", "Beta", 20.0, stock=0, rating=5, category="Travel")]
PAGE_2 = [Book("u3", "Gamma", 30.5)]


def crawl(site: SiteConfig, settings: Settings, limit_pages: int | None = None) -> CrawlResult:
    fetcher = build_fetcher(site, settings)
    try:
        return crawl_site(site, fetcher, GenericParser(site), limit_pages=limit_pages)
    finally:
        fetcher.close()


@respx.mock
def test_crawls_listing_and_detail_pages(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([PAGE_1, PAGE_2]).mount(respx)
    result = crawl(books_site, settings)
    assert result.pages == 2
    assert result.failures == 0
    assert [p.key for p in result.products] == ["u1", "u2", "u3"]
    beta = result.products[1]
    assert (beta.title, beta.price, beta.currency) == ("Beta", 20.0, "GBP")
    assert (beta.category, beta.stock, beta.in_stock) == ("Travel", 0, False)
    assert beta.attributes == {"rating": 5}


@respx.mock
def test_limit_pages(books_site: SiteConfig, settings: Settings) -> None:
    shop = FakeShop([PAGE_1, PAGE_2])
    shop.mount(respx)
    result = crawl(books_site, settings, limit_pages=1)
    assert [p.key for p in result.products] == ["u1", "u2"]
    assert not any("page-2" in url for url in shop.requests)


@respx.mock
def test_broken_detail_page_is_counted_not_fatal(
    books_site: SiteConfig, settings: Settings
) -> None:
    shop = FakeShop([PAGE_1, PAGE_2])
    shop.broken.add(PAGE_1[0].slug)
    shop.mount(respx)
    result = crawl(books_site, settings)
    assert [p.key for p in result.products] == ["u2", "u3"]
    assert result.failures == 1


@respx.mock
def test_duplicate_keys_keep_first(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([[Book("dup", "First", 1.0), Book("dup", "Second", 2.0)]]).mount(respx)
    result = crawl(books_site, settings)
    assert [(p.key, p.title) for p in result.products] == [("dup", "First")]


@respx.mock
def test_later_listing_failure_keeps_partial_results(
    books_site: SiteConfig, settings: Settings
) -> None:
    shop = FakeShop([PAGE_1, PAGE_2])
    shop.mount(respx)
    respx.get("https://books.toscrape.com/catalogue/page-2.html").respond(404)
    result = crawl(books_site, settings)
    assert [p.key for p in result.products] == ["u1", "u2"]
    assert result.failures == 1


@respx.mock
def test_first_listing_failure_is_fatal(books_site: SiteConfig, settings: Settings) -> None:
    respx.get("https://books.toscrape.com/robots.txt").respond(404)
    respx.get("https://books.toscrape.com/catalogue/page-1.html").respond(500)
    with pytest.raises(CrawlError, match="first listing page"):
        crawl(books_site, settings)


@respx.mock
def test_robots_disallow_is_fatal(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([PAGE_1]).mount(respx, robots="User-agent: *\nDisallow: /catalogue/\n")
    with pytest.raises(CrawlError, match=r"robots\.txt disallows"):
        crawl(books_site, settings)


@respx.mock
def test_empty_catalogue_is_an_error(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([[]]).mount(respx)
    with pytest.raises(CrawlError, match="no items found"):
        crawl(books_site, settings)


@respx.mock
def test_invalid_items_are_rejected_not_fatal(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([[Book("ok", "Fine", 5.0), Book("", "No key", 5.0)]]).mount(respx)
    result = crawl(books_site, settings)
    assert [p.key for p in result.products] == ["ok"]
    assert result.failures == 1


@respx.mock
def test_all_items_invalid_is_an_error(books_site: SiteConfig, settings: Settings) -> None:
    FakeShop([[Book("", "No key", 5.0)]]).mount(respx)
    with pytest.raises(CrawlError, match="no valid products"):
        crawl(books_site, settings)
