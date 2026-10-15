"""Plain data types shared across modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

RawItem = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Product:
    """One product as observed on a single scrape."""

    key: str
    title: str
    price: float
    currency: str
    url: str
    category: str | None = None
    stock: int | None = None
    in_stock: bool | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ListingPage:
    items: list[RawItem]
    next_url: str | None = None
    skipped: int = 0


class ChangeKind(StrEnum):
    NEW = "new"
    REMOVED = "removed"
    PRICE_CHANGE = "price_change"
    BACK_IN_STOCK = "back_in_stock"
    OUT_OF_STOCK = "out_of_stock"


@dataclass(frozen=True, slots=True)
class Change:
    kind: ChangeKind
    key: str
    title: str
    url: str
    currency: str
    old_price: float | None = None
    new_price: float | None = None

    @property
    def delta(self) -> float | None:
        if self.old_price is None or self.new_price is None:
            return None
        return self.new_price - self.old_price

    @property
    def percent(self) -> float | None:
        """Relative price change in percent, or None when it cannot be computed."""
        if self.delta is None or not self.old_price:
            return None
        return self.delta / self.old_price * 100
