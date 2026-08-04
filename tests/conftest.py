"""Общие фикстуры тестов.

Главная тонкость: db.py и auth.py читают TU4KA_DB / TU4KA_PUSH_USER /
TU4KA_PUSH_PASS в глобалы на момент импорта модуля. Переопределять переменные
окружения после импорта бесполезно — подменяем сами глобалы (db.connect() и
auth.resolve_push_device() читают их при каждом вызове, так что monkeypatch
работает).
"""

import hashlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from server import auth, db, main, migrate
from server.routes import push as push_routes

PUSH_USER = "tu4ka"
PUSH_PASS = "test-secret"


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Лимитер приёма живёт в модульном словаре, то есть переживает тест.

    Без сброса первый же тест, отправивший push, съедал бы окно у следующих:
    время в тестах истории заморожено фикстурой frozen_now, и окно само
    никогда не уезжает.
    """
    push_routes._accepted.clear()
    auth._bridge_warned = False
    yield
    push_routes._accepted.clear()


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


def sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def set_push_secret(db_path):
    """Кладёт секрет push прямо в devices — путь «БД источник правды».

    Отличие от auth_client: тот оставляет строку устройства без секрета и
    проверяет мост на переменные окружения.
    """

    def _set(secret, device_id=1, prev=None, prev_until=None):
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute(
                "UPDATE devices SET push_secret_sha256 = ?,"
                " push_secret_prev_sha256 = ?, push_secret_prev_until = ?"
                " WHERE id = ?",
                (sha256(secret), sha256(prev) if prev else None, prev_until,
                 device_id))
        conn.close()

    return _set


@pytest.fixture
def add_device(db_path):
    """Заводит второе устройство со своими кредами. Возвращает его id."""

    def _add(slug, push_user, secret, chip_id=None):
        conn = sqlite3.connect(str(db_path))
        with conn:
            cur = conn.execute(
                "INSERT INTO devices(slug, name, chip_id, push_user,"
                " push_secret_sha256, created_at) VALUES(?,?,?,?,?,?)",
                (slug, slug, chip_id, push_user, sha256(secret),
                 int(time.time())))
            device_id = cur.lastrowid
        conn.close()
        return device_id

    return _add


@pytest.fixture
def device_client(db_path, set_push_secret):
    """Клиент, где креды устройства заданы в БД, а окружение пустое."""
    set_push_secret(PUSH_PASS)
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
