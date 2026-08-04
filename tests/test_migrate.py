"""Раннер миграций: подъём с нуля, подъём с боевой базы, откат, идемпотентность.

Отдельная фикстура вместо conftest.db_path: там миграции уже прогнаны, а здесь
нужно смотреть именно на процесс — базу до, во время и после.
"""

import hashlib
import sqlite3

import pytest

from server import db, migrate, migrations

# Схема, которая была на боевой базе до появления раннера. Дословная копия v1
# держится здесь намеренно: если кто-то однажды «поправит» migrations.V1,
# тест на подъём боевой базы должен покраснеть.
LEGACY_SCHEMA = """
CREATE TABLE measurements(
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
CREATE INDEX idx_measurements_ts ON measurements(ts);
"""


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Пустой каталог под базу; миграции НЕ прогнаны."""
    path = tmp_path / "tu4ka.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    return path


def make_legacy_db(path, rows):
    """База в том виде, в каком живёт прод: user_version=0, одна таблица."""
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO measurements(id, ts, sensor_id, pm25, pm10, raw)"
            " VALUES(?,?,?,?,?,?)", rows)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def read(path, sql, args=()):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_fresh_db_reaches_target_version(fresh_db):
    assert migrate.run() == [1, 2, 3]
    assert read(fresh_db, "PRAGMA user_version")[0][0] == migrate.TARGET


def test_fresh_db_has_all_tables_and_indexes(fresh_db):
    migrate.run()
    names = {r[0] for r in read(
        fresh_db, "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"devices", "measurements", "measurements_raw"} <= names
    assert {"idx_measurements_ts", "idx_meas_dev_ts",
            "idx_devices_push_user"} <= names


def test_push_user_is_unique(fresh_db):
    """push_user — ключ маршрутизации приёма: двух устройств с одним логином
    быть не должно, иначе выбор устройства становится «какая строка попадётся»."""
    migrate.run()

    conn = sqlite3.connect(str(fresh_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "INSERT INTO devices(slug, push_user, created_at)"
                    " VALUES('другая', 'tu4ka', 0)")
    finally:
        conn.close()


def test_rerun_is_a_noop(fresh_db):
    migrate.run()
    assert migrate.run() == []
    assert read(fresh_db, "PRAGMA user_version")[0][0] == migrate.TARGET


def test_prod_shaped_db_keeps_every_row(fresh_db):
    rows = [(i, 1700000000 + i * 60, "2998975", 5.0 + i, 9.0 + i, '{"n":%d}' % i)
            for i in range(50)]
    make_legacy_db(fresh_db, rows)

    migrate.run()

    assert read(fresh_db, "SELECT count(*) FROM measurements")[0][0] == 50
    assert read(fresh_db, "SELECT count(*) FROM measurements_raw")[0][0] == 50
    # id сохранены — на них ссылается measurements_raw
    assert read(fresh_db, "SELECT id, ts, sensor_id, pm25, pm10 FROM measurements"
                          " ORDER BY id")[0] == (0, 1700000000, "2998975", 5.0, 9.0)
    assert read(fresh_db, "SELECT raw FROM measurements_raw WHERE measurement_id=7"
                )[0][0] == '{"n":7}'


def test_prod_shaped_db_puts_everything_on_device_one(fresh_db):
    make_legacy_db(fresh_db, [(1, 1700000000, "2998975", 5.0, 9.0, "{}"),
                              (2, 1700000060, "test", 6.0, 10.0, "{}")])

    migrate.run()

    assert read(fresh_db, "SELECT DISTINCT device_id FROM measurements") == [(1,)]
    # строку с sensor_id='test' не выбрасываем: данные есть данные
    assert read(fresh_db, "SELECT count(*) FROM measurements")[0][0] == 2


def test_first_device_takes_chip_id_from_the_data(fresh_db):
    make_legacy_db(fresh_db, [(1, 1700000000, "2998975", 5.0, 9.0, "{}"),
                              (2, 1700000060, "2998975", 6.0, 10.0, "{}"),
                              (3, 1700000120, "test", 7.0, 11.0, "{}")])

    migrate.run()

    assert read(fresh_db, "SELECT slug, chip_id FROM devices") == [
        ("tu4ka", "2998975")]


def test_first_device_stores_hash_of_push_pass(fresh_db, monkeypatch):
    monkeypatch.setenv("TU4KA_PUSH_USER", "tu4ka")
    monkeypatch.setenv("TU4KA_PUSH_PASS", "s3cret")

    migrate.run()

    expected = hashlib.sha256(b"s3cret").hexdigest()
    assert read(fresh_db, "SELECT push_user, push_secret_sha256 FROM devices") == [
        ("tu4ka", expected)]


def test_first_device_leaves_secret_null_without_push_pass(fresh_db, monkeypatch):
    monkeypatch.delenv("TU4KA_PUSH_PASS", raising=False)

    migrate.run()

    assert read(fresh_db, "SELECT push_secret_sha256 FROM devices") == [(None,)]


def test_failed_migration_rolls_back_and_keeps_version(fresh_db, monkeypatch):
    monkeypatch.setattr(migrate, "MIGRATIONS", (
        (1, migrations.V1),
        (2, ("CREATE TABLE half_done(x)", "THIS IS NOT SQL")),
    ))

    with pytest.raises(sqlite3.Error):
        migrate.run()

    # версия осталась на успешно применённой единице
    assert read(fresh_db, "PRAGMA user_version")[0][0] == 1
    names = {r[0] for r in read(fresh_db, "SELECT name FROM sqlite_master")}
    assert "half_done" not in names
    assert "measurements" in names


def test_duplicate_ts_stops_the_rebuild_instead_of_merging(fresh_db):
    """Уникальный индекс (device_id, ts) — не косметика.

    Если в истории найдутся два измерения на одну секунду, миграция обязана
    упасть целиком, а не решать за человека, какое из них лишнее.
    """
    make_legacy_db(fresh_db, [(1, 1700000000, "2998975", 5.0, 9.0, "{}"),
                              (2, 1700000000, "2998975", 6.0, 10.0, "{}")])

    with pytest.raises(sqlite3.IntegrityError):
        migrate.run()

    assert read(fresh_db, "PRAGMA user_version")[0][0] == 1
    assert read(fresh_db, "SELECT count(*) FROM measurements")[0][0] == 2
    # старая таблица цела: raw на месте, device_id не появился
    assert {r[1] for r in read(fresh_db, "PRAGMA table_info(measurements)")} >= {"raw"}


def test_assert_current_refuses_to_start_on_an_old_db(fresh_db):
    make_legacy_db(fresh_db, [])

    with pytest.raises(RuntimeError, match="python -m server.migrate"):
        migrate.assert_current()

    migrate.run()
    migrate.assert_current()  # после миграции молчит


def test_assert_current_refuses_to_start_on_a_newer_db(fresh_db):
    migrate.run()
    conn = sqlite3.connect(str(fresh_db))
    conn.execute(f"PRAGMA user_version = {migrate.TARGET + 1}")
    conn.close()

    with pytest.raises(RuntimeError, match="новее кода"):
        migrate.assert_current()
