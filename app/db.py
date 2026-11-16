"""SQLite storage. JSON columns hold the flexible parts (type config, template fields, run settings)."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_types (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Custom',
    description TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    page_sizes TEXT NOT NULL,
    fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    settings TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '{}'
);
"""

JSON_COLUMNS = {"config", "page_sizes", "fields", "settings", "summary"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_conn = _connect()
_conn.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLUMNS & d.keys():
        d[k] = json.loads(d[k])
    return d


def _enc(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else v


@contextmanager
def tx():
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [_row(r) for r in _conn.execute(sql, params).fetchall()]


def query_one(sql: str, params=()) -> dict | None:
    with _lock:
        return _row(_conn.execute(sql, params).fetchone())


def execute(sql: str, params=()) -> int:
    with tx() as c:
        cur = c.execute(sql, tuple(_enc(p) for p in params))
        return cur.lastrowid


def update(table: str, key_col: str, key, values: dict) -> None:
    cols = ", ".join(f"{k} = ?" for k in values)
    execute(f"UPDATE {table} SET {cols} WHERE {key_col} = ?", [*values.values(), key])
