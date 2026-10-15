from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from price_monitor.models import Change, ChangeKind, Product
from price_monitor.storage import SCHEMA_VERSION, Repository, SchemaError

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def product(key: str, price: float = 10.0, **kw: Any) -> Product:
    return Product(key, f"Item {key}", price, "GBP", f"https://x/{key}", **kw)


def run(repo: Repository, offset_hours: int, products: list[Product], site: str = "s") -> int:
    when = T0 + timedelta(hours=offset_hours)
    run_id = repo.start_run(site, when, page_limit=None, simulated=False)
    repo.record_observations(run_id, site, products, when)
    repo.finish_run(run_id, when, status="ok", products_seen=len(products))
    return run_id


def test_schema_is_versioned(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    Repository.open(path).close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()
    Repository.open(path).close()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(SchemaError, match="newer"):
        Repository.open(path)


def test_foreign_database_is_not_modified(tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError, match="no schema version"):
        Repository.open(path)


def test_observations_accumulate_and_products_upsert(repo: Repository) -> None:
    run(repo, 0, [product("a", 10.0, stock=3, in_stock=True, attributes={"rating": 4})])
    run(repo, 1, [product("a", 12.5, stock=0, in_stock=False, category="Poetry")])
    (state,) = repo.current_products("s")
    assert state.price == 12.5
    assert state.in_stock is False
    assert state.category == "Poetry"
    points = repo.history(state.id)
    assert [p.price for p in points] == [10.0, 12.5]
    assert [p.stock for p in points] == [3, 0]
    assert repo.histories([state.id])[state.id] == points


def test_attributes_roundtrip_unicode(repo: Repository) -> None:
    run(repo, 0, [product("a", attributes={"autor": "Brontë", "n": 2})])
    assert repo.current_products()[0].attributes == {"autor": "Brontë", "n": 2}


def test_latest_states_and_deactivate(repo: Repository) -> None:
    run(repo, 0, [product("a"), product("b")])
    repo.deactivate("s", ["b"])
    states = repo.latest_states("s")
    assert states["a"].active and not states["b"].active
    assert [p.key for p in repo.current_products("s")] == ["a"]
    assert {p.key for p in repo.current_products("s", active_only=False)} == {"a", "b"}
    run(repo, 1, [product("b")])
    assert repo.latest_states("s")["b"].active


def test_sites_are_isolated(repo: Repository) -> None:
    run(repo, 0, [product("a", 1.0)], site="one")
    run(repo, 0, [product("a", 2.0)], site="two")
    assert repo.latest_states("one")["a"].price == 1.0
    assert repo.latest_states("two")["a"].price == 2.0
    assert len(repo.current_products()) == 2


def test_find_products(repo: Repository) -> None:
    run(repo, 0, [product("abc"), product("abd")])
    assert [p.key for p in repo.find_products("abc")] == ["abc"]
    assert len(repo.find_products("item")) == 2
    assert repo.find_products("zzz") == []
    assert repo.find_products("item", site="other") == []


def test_runs_and_previous_run_lookup(repo: Repository) -> None:
    first = run(repo, 0, [product("a")])
    failed = repo.start_run("s", T0 + timedelta(hours=1), page_limit=2, simulated=True)
    repo.finish_run(failed, T0, status="failed", error="boom")
    third = run(repo, 2, [product("a")])
    assert repo.last_ok_run("s", before_run_id=third).id == first  # type: ignore[union-attr]
    assert repo.last_ok_run("s", before_run_id=first) is None
    assert repo.last_ok_run("s").id == third  # type: ignore[union-attr]
    assert [r.id for r in repo.runs("s")] == [first, third]
    assert repo.runs("nope") == []


def test_events_roundtrip(repo: Repository) -> None:
    run_id = run(repo, 0, [product("a"), product("b")])
    changes = [
        Change(ChangeKind.PRICE_CHANGE, "a", "Item a", "https://x/a", "GBP", 10.0, 8.0),
        Change(ChangeKind.NEW, "b", "Item b", "https://x/b", "GBP", new_price=10.0),
    ]
    repo.add_events(run_id, "s", changes, T0)
    events = repo.events_for_run(run_id)
    assert [(e.key, e.kind, e.old_price, e.new_price) for e in events] == [
        ("a", ChangeKind.PRICE_CHANGE, 10.0, 8.0),
        ("b", ChangeKind.NEW, None, 10.0),
    ]
    assert not events[0].simulated


def test_alert_log_window(repo: Repository) -> None:
    repo.record_alert("fp", "telegram", "rule", T0)
    assert repo.alert_sent_since("fp", "telegram", T0 - timedelta(hours=1))
    assert not repo.alert_sent_since("fp", "telegram", T0 + timedelta(hours=1))
    assert not repo.alert_sent_since("fp", "email", T0 - timedelta(hours=1))
    assert not repo.alert_sent_since("other", "telegram", T0 - timedelta(hours=1))


def test_all_observations_ordered(repo: Repository) -> None:
    run(repo, 0, [product("a", 1.0)])
    run(repo, 1, [product("a", 2.0)])
    assert [point.price for _, point in repo.all_observations()] == [1.0, 2.0]


def test_transaction_rolls_back(repo: Repository) -> None:
    run_id = repo.start_run("s", T0, page_limit=None, simulated=False)

    def failing_write() -> None:
        with repo.transaction():
            repo.record_observations(run_id, "s", [product("a")], T0)
            raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        failing_write()
    assert repo.current_products() == []
