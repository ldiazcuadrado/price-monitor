"""SQLite schema and versioning.

The schema version lives in ``PRAGMA user_version``. There are no migrations yet: a fresh
database is created at the current version and a database from a newer release is refused.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    page_limit INTEGER,
    simulated INTEGER NOT NULL DEFAULT 0,
    products_seen INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    url TEXT NOT NULL,
    currency TEXT NOT NULL,
    attributes TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE (site, key)
);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER,
    in_stock INTEGER,
    UNIQUE (product_id, run_id)
);
CREATE INDEX idx_observations_product ON observations (product_id, observed_at);

CREATE TABLE change_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    old_price REAL,
    new_price REAL,
    detected_at TEXT NOT NULL
);
CREATE INDEX idx_change_events_run ON change_events (run_id);

CREATE TABLE alert_log (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    notifier TEXT NOT NULL,
    rule TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE INDEX idx_alert_log_lookup ON alert_log (fingerprint, notifier, sent_at);
"""


class SchemaError(Exception):
    """The database cannot be used by this version of price-monitor."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"database schema version {version} is newer than supported version {SCHEMA_VERSION}; "
            "upgrade price-monitor"
        )
    if version == 0 and conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
        raise SchemaError("database is not empty but has no schema version; refusing to modify it")
    with conn:
        conn.executescript(DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
