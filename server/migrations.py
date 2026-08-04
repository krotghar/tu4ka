"""Список миграций схемы: (версия, шаг).

Шаг — либо кортеж SQL-инструкций, либо функция `apply(conn)`. Функция нужна
там, где значение считает питон: `push_secret_sha256` — это SHA-256 от
`TU4KA_PUSH_PASS`, чистым SQL его не получить.

Инструкции лежат по одной, а не одной строкой под `executescript()`: тот делает
COMMIT перед запуском и разорвал бы транзакцию миграции. Раннер выполняет их
через `conn.execute()` внутри своей транзакции — см. server/migrate.py.

Уже применённые миграции — история, а не код: править их задним числом нельзя,
на боевой базе они отработали ровно в том виде, в каком написаны. Изменение
схемы = новая версия в конце списка.
"""

import hashlib
import os
import time

# --- v1: схема, с которой сервис жил до появления раннера, дословно ----------
#
# Боевая база с user_version=0 структурно уже находится здесь, поэтому
# применение v1 к ней ничего не меняет (все CREATE — IF NOT EXISTS). Чистая
# и боевая базы сходятся в одной точке, и отдельная ветка «а это легаси или
# новая база?» не нужна.
V1 = (
    """CREATE TABLE IF NOT EXISTS measurements(
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts)",
)

# --- v2: устройства, device_id в измерениях, raw в своей таблице -------------

V2_DEVICES = (
    """CREATE TABLE devices(
        id                      INTEGER PRIMARY KEY,
        slug                    TEXT    NOT NULL UNIQUE,
        name                    TEXT,
        -- esp8266id прошивки: справочно, для маршрутизации push не используется
        -- никогда (человек мог перепаять плату). Расхождение не отклоняем,
        -- а помечаем chip_id_conflict_at.
        chip_id                 TEXT,
        chip_id_conflict_at     INTEGER,
        push_user               TEXT    NOT NULL,
        push_secret_sha256      TEXT,
        -- grace-период при ротации токена: старый секрет живёт до _until,
        -- иначе перенастройка датчика означала бы дыру в данных.
        push_secret_prev_sha256 TEXT,
        push_secret_prev_until  INTEGER,
        created_at              INTEGER NOT NULL
    )""",
)

# Порядок перестроения выбран так, чтобы ни в один момент не существовало
# ссылки на отсутствующую таблицу: старая уезжает под именем measurements_v1,
# новая создаётся сразу под финальным именем, measurements_raw ссылается на
# уже существующую measurements. Вариант «создать measurements_new, дропнуть
# старую, переименовать» ломается на ALTER TABLE RENAME: SQLite разбирает все
# объекты схемы и спотыкается о FK в measurements_raw, указывающий на только
# что удалённую таблицу.
V2_REBUILD = (
    "ALTER TABLE measurements RENAME TO measurements_v1",
    """CREATE TABLE measurements(
        id          INTEGER PRIMARY KEY,
        device_id   INTEGER NOT NULL REFERENCES devices(id),
        ts          INTEGER NOT NULL,
        sensor_id   TEXT,
        pm10        REAL,
        pm25        REAL,
        temperature REAL,
        humidity    REAL,
        pressure    REAL,
        signal      REAL
    )""",
    """INSERT INTO measurements(id, device_id, ts, sensor_id, pm10, pm25,
                                temperature, humidity, pressure, signal)
       SELECT id, 1, ts, sensor_id, pm10, pm25,
              temperature, humidity, pressure, signal
       FROM measurements_v1""",
    # raw — 79% объёма горячей таблицы (замерено на боевой базе: 9.6 МБ из
    # 12.1). Не удаляем: это страховка для будущих миграций, просто перестаёт
    # тащиться в каждый SELECT по измерениям.
    """CREATE TABLE measurements_raw(
        measurement_id INTEGER PRIMARY KEY REFERENCES measurements(id) ON DELETE CASCADE,
        raw            TEXT NOT NULL
    )""",
    "INSERT INTO measurements_raw(measurement_id, raw) SELECT id, raw FROM measurements_v1",
    "DROP TABLE measurements_v1",
    "CREATE INDEX idx_measurements_ts ON measurements(ts)",
    # Дублей по ts на боевой базе нет (проверено на снимке от 2026-08-04:
    # 0 из 14 899). Если они где-то найдутся, миграция упадёт здесь целиком
    # и версия не сдвинется — это правильнее, чем молча их склеить.
    "CREATE UNIQUE INDEX idx_meas_dev_ts ON measurements(device_id, ts)",
)


def _first_device(conn) -> None:
    """Заводит устройство 1 — ту самую тучку, что уже шлёт на этот сервер.

    Креды читаются из окружения, а не из server.auth: миграция должна быть
    самодостаточной и не зависеть от того, что успел прочитать в свои глобалы
    соседний модуль на импорте.

    Пустой TU4KA_PUSH_PASS (тесты, свежая машина) — норма: кладём NULL. Проверка
    кредов на этой версии схемы ещё не смотрит в devices, роутинг push по
    устройству приходит следующей сессией.
    """
    push_user = os.environ.get("TU4KA_PUSH_USER") or "tu4ka"
    push_pass = os.environ.get("TU4KA_PUSH_PASS") or ""
    secret = hashlib.sha256(push_pass.encode()).hexdigest() if push_pass else None
    # chip_id берём из самих данных: тот esp8266id, что прислал больше всего
    # измерений. На пустой базе останется NULL и заполнится первым push-ом.
    row = conn.execute(
        "SELECT sensor_id FROM measurements WHERE sensor_id IS NOT NULL"
        " AND sensor_id <> '' GROUP BY sensor_id ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO devices(id, slug, name, chip_id, push_user,"
        " push_secret_sha256, created_at) VALUES(1,'tu4ka','тучка',?,?,?,?)",
        (row[0] if row else None, push_user, secret, int(time.time())),
    )


def _apply_v2(conn) -> None:
    for sql in V2_DEVICES:
        conn.execute(sql)
    _first_device(conn)
    for sql in V2_REBUILD:
        conn.execute(sql)


MIGRATIONS = (
    (1, V1),
    (2, _apply_v2),
)
