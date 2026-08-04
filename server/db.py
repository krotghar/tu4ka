"""SQLite: подключение и прагмы.

DB_PATH читается в глобал на импорте модуля — тесты подменяют именно его
(monkeypatch.setattr(db, "DB_PATH", ...)), поэтому обращаться к нему нужно
через модуль, а не через `from .db import DB_PATH`.

Схемы здесь больше нет: она версионируется миграциями (server/migrations.py),
а применяет их отдельный процесс до старта приложения — см. server/migrate.py.
"""

import os
import sqlite3

DB_PATH = os.environ.get("TU4KA_DB", "/var/lib/tu4ka/tu4ka.db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Ждать снятия лока, а не падать сразу: писатель у нас один (push), но
    # рядом ходят бэкап и миграции.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-65536")  # 64 МБ, отрицательное = килобайты
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn
