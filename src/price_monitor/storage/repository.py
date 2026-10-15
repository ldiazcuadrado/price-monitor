"""SQLite persistence for runs, products, observations, change events and the alert log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from price_monitor.models import Change, ChangeKind, Product
from price_monitor.storage.records import EventRecord, HistoryPoint, ProductState, RunRecord
from price_monitor.storage.schema import ensure_schema


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _opt_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        site=row["site"],
        started_at=_parse(row["started_at"]),
        finished_at=_parse(row["finished_at"]) if row["finished_at"] else None,
        status=row["status"],
        page_limit=row["page_limit"],
        simulated=bool(row["simulated"]),
        products_seen=row["products_seen"],
        failures=row["failures"],
    )


def _state(row: sqlite3.Row) -> ProductState:
    return ProductState(
        id=row["id"],
        site=row["site"],
        key=row["key"],
        title=row["title"],
        category=row["category"],
        url=row["url"],
        currency=row["currency"],
        attributes=json.loads(row["attributes"]),
        active=bool(row["active"]),
        price=row["price"],
        stock=row["stock"],
        in_stock=_opt_bool(row["in_stock"]),
        observed_at=_parse(row["observed_at"]),
    )


_LATEST_STATE_SQL = """
SELECT p.id, p.site, p.key, p.title, p.category, p.url, p.currency, p.attributes, p.active,
       o.price, o.stock, o.in_stock, o.observed_at
FROM products p
JOIN observations o ON o.id = (
    SELECT id FROM observations WHERE product_id = p.id ORDER BY observed_at DESC, id DESC LIMIT 1
)
"""


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._depth = 0

    @classmethod
    def open(cls, path: Path | str) -> Self:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        ensure_schema(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit on success and roll back on error; nested uses join the outer transaction."""
        if self._depth:
            yield
            return
        self._depth += 1
        try:
            with self._conn:
                yield
        finally:
            self._depth -= 1

    # runs -----------------------------------------------------------------

    def start_run(
        self, site: str, started_at: datetime, *, page_limit: int | None, simulated: bool
    ) -> int:
        with self.transaction():
            cursor = self._conn.execute(
                "INSERT INTO runs (site, started_at, status, page_limit, simulated)"
                " VALUES (?, ?, 'running', ?, ?)",
                (site, _ts(started_at), page_limit, int(simulated)),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("INSERT did not return a row id")
        return cursor.lastrowid

    def finish_run(
        self,
        run_id: int,
        finished_at: datetime,
        *,
        status: str,
        products_seen: int = 0,
        failures: int = 0,
        error: str | None = None,
    ) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, products_seen = ?, failures = ?,"
                " error = ? WHERE id = ?",
                (_ts(finished_at), status, products_seen, failures, error, run_id),
            )

    def last_ok_run(self, site: str, before_run_id: int | None = None) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE site = ? AND status = 'ok' AND id < ?"
            " ORDER BY id DESC LIMIT 1",
            (site, before_run_id if before_run_id is not None else 2**62),
        ).fetchone()
        return _run(row) if row else None

    def runs(self, site: str | None = None) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE (? IS NULL OR site = ?) AND status = 'ok' ORDER BY id",
            (site, site),
        ).fetchall()
        return [_run(r) for r in rows]

    # products and observations ---------------------------------------------

    def latest_states(self, site: str) -> dict[str, ProductState]:
        rows = self._conn.execute(_LATEST_STATE_SQL + " WHERE p.site = ?", (site,)).fetchall()
        return {r["key"]: _state(r) for r in rows}

    def record_observations(
        self, run_id: int, site: str, products: Iterable[Product], observed_at: datetime
    ) -> dict[str, int]:
        """Upsert products and append one observation each. Returns key -> product id."""
        stamp = _ts(observed_at)
        ids: dict[str, int] = {}
        with self.transaction():
            for p in products:
                self._conn.execute(
                    "INSERT INTO products (site, key, title, category, url, currency, attributes,"
                    " active, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
                    " ON CONFLICT (site, key) DO UPDATE SET title = excluded.title,"
                    " category = excluded.category, url = excluded.url,"
                    " currency = excluded.currency, attributes = excluded.attributes,"
                    " active = 1, last_seen = excluded.last_seen",
                    (
                        site,
                        p.key,
                        p.title,
                        p.category,
                        p.url,
                        p.currency,
                        json.dumps(dict(p.attributes), ensure_ascii=False, default=str),
                        stamp,
                        stamp,
                    ),
                )
                product_id = self._conn.execute(
                    "SELECT id FROM products WHERE site = ? AND key = ?", (site, p.key)
                ).fetchone()["id"]
                self._conn.execute(
                    "INSERT INTO observations (product_id, run_id, observed_at, price, stock,"
                    " in_stock) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        product_id,
                        run_id,
                        stamp,
                        p.price,
                        p.stock,
                        None if p.in_stock is None else int(p.in_stock),
                    ),
                )
                ids[p.key] = product_id
        return ids

    def deactivate(self, site: str, keys: Iterable[str]) -> None:
        with self.transaction():
            self._conn.executemany(
                "UPDATE products SET active = 0 WHERE site = ? AND key = ?",
                [(site, key) for key in keys],
            )

    def product_ids(self, site: str) -> dict[str, int]:
        rows = self._conn.execute("SELECT key, id FROM products WHERE site = ?", (site,)).fetchall()
        return {r["key"]: r["id"] for r in rows}

    def current_products(
        self, site: str | None = None, *, active_only: bool = True
    ) -> list[ProductState]:
        sql = _LATEST_STATE_SQL + " WHERE (? IS NULL OR p.site = ?)"
        if active_only:
            sql += " AND p.active = 1"
        rows = self._conn.execute(sql + " ORDER BY p.site, p.title", (site, site)).fetchall()
        return [_state(r) for r in rows]

    def find_products(self, query: str, site: str | None = None) -> list[ProductState]:
        """Match by exact key first, then by case-insensitive title substring."""
        exact = [p for p in self.current_products(site, active_only=False) if p.key == query]
        if exact:
            return exact
        needle = query.lower()
        return [
            p for p in self.current_products(site, active_only=False) if needle in p.title.lower()
        ]

    def history(self, product_id: int) -> list[HistoryPoint]:
        rows = self._conn.execute(
            "SELECT run_id, observed_at, price, stock, in_stock FROM observations"
            " WHERE product_id = ? ORDER BY observed_at, id",
            (product_id,),
        ).fetchall()
        return [_point(r) for r in rows]

    def histories(self, product_ids: Sequence[int]) -> dict[int, list[HistoryPoint]]:
        return {pid: self.history(pid) for pid in product_ids}

    def all_observations(self, site: str | None = None) -> list[tuple[ProductState, HistoryPoint]]:
        states = {p.id: p for p in self.current_products(site, active_only=False)}
        rows = self._conn.execute(
            "SELECT product_id, run_id, observed_at, price, stock, in_stock FROM observations"
            " ORDER BY observed_at, id"
        ).fetchall()
        return [(states[r["product_id"]], _point(r)) for r in rows if r["product_id"] in states]

    # change events ---------------------------------------------------------

    def add_events(
        self, run_id: int, site: str, changes: Iterable[Change], detected_at: datetime
    ) -> None:
        ids = self.product_ids(site)
        with self.transaction():
            self._conn.executemany(
                "INSERT INTO change_events (run_id, product_id, kind, old_price, new_price,"
                " detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (run_id, ids[c.key], c.kind.value, c.old_price, c.new_price, _ts(detected_at))
                    for c in changes
                ],
            )

    def events_for_run(self, run_id: int) -> list[EventRecord]:
        rows = self._conn.execute(
            "SELECT e.*, p.site, p.key, p.title, p.currency, r.simulated FROM change_events e"
            " JOIN products p ON p.id = e.product_id JOIN runs r ON r.id = e.run_id"
            " WHERE e.run_id = ? ORDER BY e.id",
            (run_id,),
        ).fetchall()
        return [
            EventRecord(
                run_id=r["run_id"],
                site=r["site"],
                kind=ChangeKind(r["kind"]),
                key=r["key"],
                title=r["title"],
                currency=r["currency"],
                old_price=r["old_price"],
                new_price=r["new_price"],
                detected_at=_parse(r["detected_at"]),
                simulated=bool(r["simulated"]),
            )
            for r in rows
        ]

    # alert log ---------------------------------------------------------------

    def alert_sent_since(self, fingerprint: str, notifier: str, since: datetime) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM alert_log WHERE fingerprint = ? AND notifier = ? AND sent_at >= ?"
            " LIMIT 1",
            (fingerprint, notifier, _ts(since)),
        ).fetchone()
        return row is not None

    def record_alert(self, fingerprint: str, notifier: str, rule: str, sent_at: datetime) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO alert_log (fingerprint, notifier, rule, sent_at) VALUES (?, ?, ?, ?)",
                (fingerprint, notifier, rule, _ts(sent_at)),
            )


def _point(row: Any) -> HistoryPoint:
    return HistoryPoint(
        run_id=row["run_id"],
        observed_at=_parse(row["observed_at"]),
        price=row["price"],
        stock=row["stock"],
        in_stock=_opt_bool(row["in_stock"]),
    )
