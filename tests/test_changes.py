from __future__ import annotations

from datetime import UTC, datetime

import pytest

from price_monitor.changes import detect_changes, meets_thresholds
from price_monitor.config import Thresholds
from price_monitor.models import Change, ChangeKind, Product
from price_monitor.storage.records import ProductState

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def product(key: str, price: float = 10.0, in_stock: bool | None = True) -> Product:
    return Product(key, f"Item {key}", price, "EUR", f"https://x/{key}", in_stock=in_stock)


def state(
    key: str, price: float = 10.0, in_stock: bool | None = True, active: bool = True
) -> ProductState:
    return ProductState(
        id=1,
        site="s",
        key=key,
        title=f"Item {key}",
        category=None,
        url=f"https://x/{key}",
        currency="EUR",
        attributes={},
        active=active,
        price=price,
        stock=None,
        in_stock=in_stock,
        observed_at=NOW,
    )


def kinds(changes: list[Change]) -> list[tuple[str, ChangeKind]]:
    return [(c.key, c.kind) for c in changes]


def test_detects_every_kind() -> None:
    previous = {
        "same": state("same"),
        "price": state("price", 10.0),
        "gone": state("gone", 4.0),
        "oos": state("oos", in_stock=True),
        "back": state("back", in_stock=False),
    }
    current = [
        product("same"),
        product("price", 12.0),
        product("oos", in_stock=False),
        product("back", in_stock=True),
        product("fresh", 3.0),
    ]
    changes = detect_changes(previous, current, Thresholds())
    assert kinds(changes) == [
        ("price", ChangeKind.PRICE_CHANGE),
        ("oos", ChangeKind.OUT_OF_STOCK),
        ("back", ChangeKind.BACK_IN_STOCK),
        ("fresh", ChangeKind.NEW),
        ("gone", ChangeKind.REMOVED),
    ]
    price = changes[0]
    assert (price.old_price, price.new_price, price.delta) == (10.0, 12.0, 2.0)
    assert price.percent == pytest.approx(20.0)
    assert changes[-1].old_price == 4.0


def test_no_changes() -> None:
    assert detect_changes({"a": state("a")}, [product("a")], Thresholds()) == []


def test_float_noise_is_not_a_change() -> None:
    assert detect_changes({"a": state("a", 0.1 + 0.2)}, [product("a", 0.3)], Thresholds()) == []


def test_unknown_stock_is_ignored() -> None:
    previous = {"a": state("a", in_stock=None)}
    assert detect_changes(previous, [product("a", in_stock=False)], Thresholds()) == []


def test_reappearing_product_counts_as_new() -> None:
    changes = detect_changes({"a": state("a", active=False)}, [product("a")], Thresholds())
    assert kinds(changes) == [("a", ChangeKind.NEW)]


def test_inactive_products_are_not_reported_removed_again() -> None:
    assert detect_changes({"a": state("a", active=False)}, [], Thresholds()) == []


def test_membership_tracking_can_be_disabled() -> None:
    previous = {"gone": state("gone"), "a": state("a", 10.0)}
    current = [product("a", 11.0), product("fresh")]
    changes = detect_changes(previous, current, Thresholds(), track_membership=False)
    assert kinds(changes) == [("a", ChangeKind.PRICE_CHANGE)]


@pytest.mark.parametrize(
    ("thresholds", "new", "expected"),
    [
        (Thresholds(), 10.01, True),
        (Thresholds(min_percent=5), 10.4, False),
        (Thresholds(min_percent=5), 10.5, True),
        (Thresholds(min_absolute=1), 10.5, False),
        (Thresholds(min_absolute=1), 8.9, True),
        (Thresholds(min_percent=5, min_absolute=1), 10.6, False),
        (Thresholds(min_percent=5, min_absolute=0.1), 10.6, True),
    ],
)
def test_thresholds(thresholds: Thresholds, new: float, expected: bool) -> None:
    assert meets_thresholds(10.0, new, thresholds) is expected
    changes = detect_changes({"a": state("a", 10.0)}, [product("a", new)], thresholds)
    assert bool(changes) is expected


def test_percent_threshold_with_zero_old_price() -> None:
    assert meets_thresholds(0.0, 5.0, Thresholds(min_percent=50))


def test_change_percent_edge_cases() -> None:
    assert Change(ChangeKind.NEW, "k", "t", "u", "EUR", new_price=5.0).percent is None
    assert Change(ChangeKind.PRICE_CHANGE, "k", "t", "u", "EUR", 0.0, 5.0).percent is None
