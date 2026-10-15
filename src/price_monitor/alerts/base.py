"""Alert value type and the notifier interface."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from price_monitor.config import Rule
from price_monitor.models import Change, ChangeKind


class NotifierError(Exception):
    """Delivery failed. Messages must never contain credentials."""


@dataclass(frozen=True, slots=True)
class Alert:
    rule: Rule
    site: str
    change: Change

    @property
    def fingerprint(self) -> str:
        c = self.change
        return "|".join(
            [self.rule.name, self.site, c.key, c.kind.value, _num(c.old_price), _num(c.new_price)]
        )

    def text(self) -> str:
        c = self.change
        price = _money(c.new_price, c.currency)
        match c.kind:
            case ChangeKind.PRICE_CHANGE:
                direction = "drop" if (c.delta or 0) < 0 else "increase"
                pct = f" ({c.percent:+.1f}%)" if c.percent is not None else ""
                before = _money(c.old_price, c.currency)
                headline = f"Price {direction}: {c.title}: {before} -> {price}{pct}"
            case ChangeKind.NEW:
                headline = f"New product: {c.title} at {price}"
            case ChangeKind.REMOVED:
                headline = f"Removed: {c.title} (last price {_money(c.old_price, c.currency)})"
            case ChangeKind.BACK_IN_STOCK:
                headline = f"Back in stock: {c.title} at {price}"
            case ChangeKind.OUT_OF_STOCK:
                headline = f"Out of stock: {c.title}"
        return f"{headline}\n{c.url}  [{self.rule.name}, {self.site}]"

    def to_dict(self) -> dict[str, Any]:
        c = self.change
        return {
            "rule": self.rule.name,
            "site": self.site,
            "kind": c.kind.value,
            "key": c.key,
            "title": c.title,
            "url": c.url,
            "currency": c.currency,
            "old_price": c.old_price,
            "new_price": c.new_price,
            "percent_change": None if c.percent is None else round(c.percent, 2),
        }


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _money(value: float | None, currency: str) -> str:
    return "n/a" if value is None else f"{value:.2f} {currency}"


def render_digest(alerts: Sequence[Alert]) -> str:
    header = f"price-monitor: {len(alerts)} alert{'s' if len(alerts) != 1 else ''}"
    return header + "\n\n" + "\n\n".join(a.text() for a in alerts)


class Notifier(Protocol):
    name: str

    def send(self, alerts: Sequence[Alert]) -> None:
        """Deliver ``alerts`` as one message; raise NotifierError on failure."""
        ...
