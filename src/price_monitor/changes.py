"""Change detection between the last known state of a site and a fresh scrape."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from price_monitor.config import Thresholds
from price_monitor.models import Change, ChangeKind, Product
from price_monitor.storage.records import ProductState


def _prices_differ(old: float, new: float) -> bool:
    return not math.isclose(round(old, 4), round(new, 4), abs_tol=1e-9)


def meets_thresholds(old: float, new: float, thresholds: Thresholds) -> bool:
    """True when the move is at least every configured threshold (or none are configured)."""
    delta = abs(new - old)
    if thresholds.min_absolute is not None and delta < thresholds.min_absolute:
        return False
    if thresholds.min_percent is not None:
        percent = delta / old * 100 if old else math.inf
        if percent < thresholds.min_percent:
            return False
    return True


def detect_changes(
    previous: Mapping[str, ProductState],
    current: Iterable[Product],
    thresholds: Thresholds,
    *,
    track_membership: bool = True,
) -> list[Change]:
    """Compare ``current`` against ``previous`` (keyed by product key).

    Products inactive in ``previous`` count as new when they reappear. With
    ``track_membership`` off, only price and stock changes of known products are reported.
    """
    changes: list[Change] = []
    seen: set[str] = set()
    for product in current:
        seen.add(product.key)
        before = previous.get(product.key)
        if before is None or not before.active:
            if track_membership:
                changes.append(_change(ChangeKind.NEW, product, new_price=product.price))
            continue
        if _prices_differ(before.price, product.price) and meets_thresholds(
            before.price, product.price, thresholds
        ):
            changes.append(_change(ChangeKind.PRICE_CHANGE, product, before.price, product.price))
        if before.in_stock is not None and product.in_stock is not None:
            if before.in_stock and not product.in_stock:
                changes.append(
                    _change(ChangeKind.OUT_OF_STOCK, product, before.price, product.price)
                )
            elif not before.in_stock and product.in_stock:
                changes.append(
                    _change(ChangeKind.BACK_IN_STOCK, product, before.price, product.price)
                )
    if track_membership:
        for key, state in previous.items():
            if state.active and key not in seen:
                changes.append(
                    Change(
                        ChangeKind.REMOVED,
                        key,
                        state.title,
                        state.url,
                        state.currency,
                        old_price=state.price,
                    )
                )
    return changes


def _change(
    kind: ChangeKind,
    product: Product,
    old_price: float | None = None,
    new_price: float | None = None,
) -> Change:
    return Change(
        kind=kind,
        key=product.key,
        title=product.title,
        url=product.url,
        currency=product.currency,
        old_price=old_price,
        new_price=new_price if new_price is not None else product.price,
    )
