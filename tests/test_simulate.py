from __future__ import annotations

from datetime import UTC, datetime

from price_monitor.changes import detect_changes
from price_monitor.config import Thresholds
from price_monitor.models import ChangeKind, Product
from price_monitor.simulate import simulate_changes
from price_monitor.storage.records import ProductState


def catalogue(n: int) -> list[Product]:
    return [
        Product(f"k{i}", f"Item {i}", 10.0 + i, "GBP", f"https://x/{i}", stock=5, in_stock=True)
        for i in range(n)
    ]


def test_deterministic_and_input_untouched() -> None:
    data = catalogue(100)
    assert simulate_changes(data, 7) == simulate_changes(data, 7)
    assert simulate_changes(data, 7) != simulate_changes(data, 8)
    assert data == catalogue(100)


def test_small_catalogues_are_left_alone() -> None:
    data = catalogue(4)
    assert simulate_changes(data, 1) == data


def test_produces_every_kind_of_change() -> None:
    data = catalogue(100)
    previous = {
        p.key: ProductState(
            1,
            "s",
            p.key,
            p.title,
            None,
            p.url,
            "GBP",
            {},
            True,
            p.price,
            5,
            True,
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        for p in data
    }
    kinds = {c.kind for c in detect_changes(previous, simulate_changes(data, 1), Thresholds())}
    assert kinds == {
        ChangeKind.PRICE_CHANGE,
        ChangeKind.OUT_OF_STOCK,
        ChangeKind.NEW,
        ChangeKind.REMOVED,
    }
