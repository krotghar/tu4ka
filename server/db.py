"""SQLite: подключение, прагмы, схема.

DB_PATH читается в глобал на импорте модуля — тесты подменяют именно его
(monkeypatch.setattr(db, "DB_PATH", ...)), поэтому обращаться к нему нужно
через модуль, а не через `from .db import DB_PATH`.
"""

import os
import sqlite3
from contextlib import closing

DB_PATH = os.environ.get("TU4KA_DB", "/var/lib/tu4ka/tu4ka.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements(
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    sensor_id   TEXT,
    pm10        REAL,
    pm25        REAL,
    temperature REAL,
    humidity    REAL,
    pressure    REAL,
    signal      REAL,
    raw         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
