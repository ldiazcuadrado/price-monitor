"""Row types returned by the repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from price_monitor.models import ChangeKind


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: int
    site: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    page_limit: int | None
    simulated: bool
    products_seen: int
    failures: int


@dataclass(frozen=True, slots=True)
class ProductState:
    """A product together with its most recent observation."""

    id: int
    site: str
    key: str
    title: str
    category: str | None
    url: str
    currency: str
    attributes: Mapping[str, Any]
    active: bool
    price: float
    stock: int | None
    in_stock: bool | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    run_id: int
    observed_at: datetime
    price: float
    stock: int | None
    in_stock: bool | None


@dataclass(frozen=True, slots=True)
class EventRecord:
    run_id: int
    site: str
    kind: ChangeKind
    key: str
    title: str
    currency: str
    old_price: float | None
    new_price: float | None
    detected_at: datetime
    simulated: bool
