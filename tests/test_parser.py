from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from price_monitor.config import ConfigError, SiteConfig, parse_config
from price_monitor.models import Product
from price_monitor.parse import GenericParser, ParseError, load_parser, to_product
from tests.conftest import FIXTURES
from tests.helpers import Book, detail_html

PAGE_URL = "https://books.toscrape.com/catalogue/page-2.html"


def test_parse_real_listing_page(books_site: SiteConfig) -> None:
    html = (FIXTURES / "listing_page.html").read_text(encoding="utf-8")
    page = GenericParser(books_site).parse_listing(html, PAGE_URL, 2)
    assert len(page.items) == 20
    first = page.items[0]
    assert first["title"] == "In Her Wake"
    assert first["rating"] == 1
    assert isinstance(first["price"], float)
    assert first["url"] == "https://books.toscrape.com/catalogue/in-her-wake_980/index.html"
    assert page.next_url == "https://books.toscrape.com/catalogue/page-3.html"
    assert page.skipped == 0


def test_parse_real_detail_page(books_site: SiteConfig) -> None:
    html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")
    url = "https://books.toscrape.com/catalogue/its-only-the-himalayas_981/index.html"
    detail = GenericParser(books_site).parse_detail(html, url, {})
    assert detail == {"upc": "a22124811bfa8350", "category": "Travel", "stock": 19}


def test_out_of_stock_uses_default(books_site: SiteConfig) -> None:
    html = detail_html(Book("u1", "Gone", 5.0, stock=0))
    detail = GenericParser(books_site).parse_detail(html, "https://x", {})
    assert detail["stock"] == 0


def test_last_page_has_no_next_link(books_site: SiteConfig) -> None:
    page = GenericParser(books_site).parse_listing("<html></html>", PAGE_URL, 1)
    assert page.items == []
    assert page.next_url is None


def test_pagination_page_cap(books_site: SiteConfig) -> None:
    html = (FIXTURES / "listing_page.html").read_text(encoding="utf-8")
    capped = books_site.model_copy(
        update={"pagination": books_site.pagination.model_copy(update={"max_pages": 2})}  # type: ignore[union-attr]
    )
    assert GenericParser(capped).parse_listing(html, PAGE_URL, 2).next_url is None


def _site(extra: str = "", pagination: str = "") -> SiteConfig:
    text = textwrap.dedent(
        """
        start_urls: [https://shop.test/list]
        currency: USD
        key: sku
        item_selector: div.item
        fields:
          title: {css: h2}
          sku: {attr: data-sku}
          url: {css: a, attr: href}
          price: {css: .price, transforms: [{type: price}]}
          missing: {css: .nothing, default: fallback}
        """
    )
    import yaml

    data = yaml.safe_load(text)
    if extra:
        data.update(yaml.safe_load(extra))
    if pagination:
        data["pagination"] = yaml.safe_load(pagination)
    return parse_config({"sites": {"shop": data}}).sites["shop"]


def _item(sku: str, title: str, price: str) -> str:
    return (
        f'<div class="item" data-sku="{sku}"><h2>{title}</h2><a href="/p/{sku[1:]}">x</a>'
        f'<span class="price">{price}</span></div>'
    )


ITEMS = _item("A1", "One", "$5.00") + _item("A2", "Two", "$7.50") + _item("A3", "Broken", "ask us")


def test_item_attribute_defaults_and_skipping() -> None:
    page = GenericParser(_site()).parse_listing(ITEMS, "https://shop.test/list", 1)
    assert [i["sku"] for i in page.items] == ["A1", "A2"]
    assert page.items[0]["missing"] == "fallback"
    assert page.items[1]["url"] == "https://shop.test/p/2"
    assert page.skipped == 1


def test_url_template_pagination() -> None:
    site = _site(
        pagination=(
            "{type: url_template, template: 'https://shop.test/list?page={page}', max_pages: 3}"
        )
    )
    parser = GenericParser(site)
    assert parser.parse_listing(ITEMS, "u", 1).next_url == "https://shop.test/list?page=2"
    assert parser.parse_listing(ITEMS, "u", 3).next_url is None


def test_missing_next_link_returns_none() -> None:
    site = _site(pagination="{type: next_link, selector: a.next}")
    assert GenericParser(site).parse_listing(ITEMS, "https://shop.test/list", 1).next_url is None


def test_listing_without_url_field_falls_back_to_page_url() -> None:
    site = _site()
    object.__setattr__(site, "fields", {k: v for k, v in site.fields.items() if k != "url"})
    items = GenericParser(site).parse_listing(ITEMS, "https://shop.test/list", 1).items
    assert items[0]["url"] == "https://shop.test/list"


def test_to_product_builds_validated_record(books_site: SiteConfig) -> None:
    raw = {
        "title": " Book ",
        "price": "12.5",
        "upc": "u1",
        "stock": "4",
        "category": "X",
        "url": "https://x/1",
        "rating": 4,
    }
    product = to_product(books_site, raw)
    assert product == Product(
        key="u1",
        title="Book",
        price=12.5,
        currency="GBP",
        url="https://x/1",
        category="X",
        stock=4,
        in_stock=True,
        attributes={"rating": 4},
    )
    assert to_product(books_site, {**raw, "stock": 0}).in_stock is False
    assert to_product(books_site, {**raw, "in_stock": False}).in_stock is False
    assert to_product(books_site, {k: v for k, v in raw.items() if k != "stock"}).in_stock is None


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"title": " "}, "missing title"),
        ({"price": "abc"}, "invalid price"),
        ({"price": -1}, "negative price"),
        ({"upc": ""}, "missing key"),
        ({"stock": "many"}, "not an integer"),
    ],
)
def test_to_product_rejects(books_site: SiteConfig, patch: dict[str, object], message: str) -> None:
    raw = {"title": "T", "price": 1.0, "upc": "u", "stock": 1, "url": "https://x"} | patch
    with pytest.raises(ParseError, match=message):
        to_product(books_site, raw)


def test_load_parser_generic_and_plugin(
    books_site: SiteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(load_parser(books_site), GenericParser)
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent / "examples"))
    plugin_site = books_site.model_copy(update={"plugin": "custom_parser:JsonLdParser"})
    parser = load_parser(plugin_site)
    html = (
        '<script type="application/ld+json">{"@type": "Product", "name": "Lamp", "sku": "L1",'
        ' "offers": {"price": "9.5", "availability": "https://schema.org/InStock"}}</script>'
    )
    page = parser.parse_listing(html, "https://x/list", 1)
    assert page.items[0]["title"] == "Lamp"
    assert page.items[0]["in_stock"] is True
    assert parser.parse_detail("", "u", {}) == {}


def bad_factory(site: SiteConfig) -> object:
    return object()


def test_load_parser_plugin_errors(books_site: SiteConfig) -> None:
    for ref, message in [
        ("no_such_module_xyz:Thing", "cannot import"),
        ("price_monitor.models:Nope", "no attribute"),
        ("tests.test_parser:bad_factory", "must provide"),
    ]:
        site = books_site.model_copy(update={"plugin": ref})
        with pytest.raises(ConfigError, match=message):
            load_parser(site)
