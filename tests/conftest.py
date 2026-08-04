"""Общие фикстуры тестов.

Главная тонкость: db.py и auth.py читают TU4KA_DB / TU4KA_PUSH_USER /
TU4KA_PUSH_PASS в глобалы на момент импорта модуля. Переопределять переменные
окружения после импорта бесполезно — подменяем сами глобалы (db.connect() и
auth.check_push_auth() читают их при каждом вызове, так что monkeypatch работает).
"""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from server import auth, db, main, migrate

PUSH_USER = "tu4ka"
PUSH_PASS = "test-secret"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Уводим БД из /var/lib/tu4ka во временный каталог теста.

    Заодно гасим креды push: TU4KA_PUSH_USER/TU4KA_PUSH_PASS — штатные переменные
    проекта (лежат в /etc/tu4ka/env), и если они окажутся в окружении, auth.py
    подхватит их на импорте и включит basic-auth. Без этого сброса на такой машине
    посыпались бы все push-тесты, работающие через фикстуру client.
    """
    path = tmp_path / "tu4ka.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(auth, "PUSH_USER", "")
    monkeypatch.setattr(auth, "PUSH_PASS", "")
    # Схему создаёт раннер миграций, а не lifespan: приложение теперь только
    # сверяет версию и на пустой базе отказалось бы стартовать.
    migrate.run()
    return path


@pytest.fixture
def client(db_path):
    """TestClient обязательно как контекст-менеджер: иначе не отработает
    lifespan и в БД не будет схемы."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def auth_client(db_path, monkeypatch):
    """Клиент на приложении с включённой basic-авторизацией push."""
    monkeypatch.setattr(auth, "PUSH_USER", PUSH_USER)
    monkeypatch.setattr(auth, "PUSH_PASS", PUSH_PASS)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def insert_measurement(db_path, client):
    """Пишет измерение напрямую в БД с произвольным ts — для истории и NowCast,
    где нужны точки в прошлом (через POST /push ts всегда = now)."""

    def _insert(ts=None, pm25=None, pm10=None, temperature=None,
                humidity=None, pressure=None, sensor_id="2998975", device_id=1):
        ts = int(time.time()) if ts is None else int(ts)
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute(
                "INSERT INTO measurements(device_id, ts, sensor_id, pm10, pm25,"
                " temperature, humidity, pressure, signal)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (device_id, ts, sensor_id, pm10, pm25, temperature, humidity,
                 pressure, None),
            )
        conn.close()
        return ts

    return _insert
