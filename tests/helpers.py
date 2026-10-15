"""HTML builders mimicking the books.toscrape.com structure, for network-free tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

RATING_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five"]


@dataclass(frozen=True)
class Book:
    upc: str
    title: str
    price: float
    stock: int = 10
    rating: int = 3
    category: str = "Poetry"

    @property
    def slug(self) -> str:
        return f"{self.title.lower().replace(' ', '-')}_{self.upc}"


def listing_html(books: list[Book], next_href: str | None = None) -> str:
    pods = "".join(
        f'<article class="product_pod"><p class="star-rating {RATING_WORDS[b.rating]}"></p>'
        f'<h3><a href="{b.slug}/index.html" title="{b.title}">{b.title[:10]}</a></h3>'
        f'<div class="product_price"><p class="price_color">£{b.price:.2f}</p></div></article>'
        for b in books
    )
    nxt = (
        f'<ul class="pager"><li class="next"><a href="{next_href}">next</a></li></ul>'
        if next_href
        else ""
    )
    return f"<html><body><section>{pods}</section>{nxt}</body></html>"


def detail_html(book: Book) -> str:
    availability = f"In stock ({book.stock} available)" if book.stock else "Out of stock"
    return (
        '<html><body><ul class="breadcrumb"><li><a>Home</a></li><li><a>Books</a></li>'
        f"<li><a>{book.category}</a></li><li class='active'>{book.title}</li></ul>"
        '<table class="table table-striped">'
        f"<tr><th>UPC</th><td>{book.upc}</td></tr>"
        "<tr><th>Product Type</th><td>Books</td></tr>"
        f"<tr><th>Availability</th><td>{availability}</td></tr></table>"
        f'<p class="instock availability">{availability}</p></body></html>'
    )


class FakeShop:
    """An in-memory books.toscrape.com whose catalogue can be edited between runs."""

    base = "https://books.toscrape.com"

    def __init__(self, pages: list[list[Book]]) -> None:
        self.pages = pages
        self.broken: set[str] = set()
        self.requests: list[str] = []

    def books(self) -> dict[str, Book]:
        return {b.slug: b for page in self.pages for b in page}

    def mount(self, router: Any, *, robots: str | None = None) -> None:
        if robots is None:
            router.get(f"{self.base}/robots.txt").respond(404)
        else:
            router.get(f"{self.base}/robots.txt").respond(200, text=robots)
        for index in range(len(self.pages)):
            router.get(f"{self.base}/catalogue/page-{index + 1}.html").mock(
                side_effect=self._listing(index)
            )
        router.get(url__regex=rf"{self.base}/catalogue/(?P<slug>[^/]+)/index\.html").mock(
            side_effect=self._detail
        )

    def _listing(self, index: int) -> Callable[[httpx.Request], httpx.Response]:
        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            has_next = index + 1 < len(self.pages)
            nxt = f"page-{index + 2}.html" if has_next else None
            return httpx.Response(200, text=listing_html(self.pages[index], nxt))

        return respond

    def _detail(self, request: httpx.Request, slug: str) -> httpx.Response:
        self.requests.append(str(request.url))
        if slug in self.broken or slug not in self.books():
            return httpx.Response(404)
        return httpx.Response(200, text=detail_html(self.books()[slug]))


def charts(worksheet: Any) -> list[Any]:
    """Charts attached to an openpyxl worksheet (the attribute is not in the type stubs)."""
    return list(worksheet._charts)
