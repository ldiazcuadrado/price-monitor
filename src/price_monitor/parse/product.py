"""Conversion of raw extracted items into validated ``Product`` records."""

from __future__ import annotations

from typing import Any

from price_monitor.config import CORE_FIELDS, SiteConfig
from price_monitor.models import Product, RawItem
from price_monitor.parse.base import ParseError


def _as_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"{name} is not an integer: {value!r}") from exc


def to_product(site: SiteConfig, raw: RawItem) -> Product:
    """Validate ``raw`` and build a Product; raises ParseError on missing or invalid data."""
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ParseError("missing title")
    try:
        price = float(raw["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"missing or invalid price: {raw.get('price')!r}") from exc
    if price < 0:
        raise ParseError(f"negative price: {price}")
    key_value = raw.get(site.key)
    key = str(key_value).strip() if key_value is not None else ""
    if not key:
        raise ParseError(f"missing key field {site.key!r}")

    stock = _as_int(raw.get("stock"), "stock")
    in_stock = raw.get("in_stock")
    if in_stock is None and stock is not None:
        in_stock = stock > 0
    category = raw.get("category")
    return Product(
        key=key,
        title=title,
        price=round(price, 4),
        currency=site.currency,
        url=str(raw.get("url", "")),
        category=str(category).strip() if category else None,
        stock=stock,
        in_stock=None if in_stock is None else bool(in_stock),
        attributes={
            k: v for k, v in raw.items() if k not in CORE_FIELDS and k != site.key and v is not None
        },
    )
