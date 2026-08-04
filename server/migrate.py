"""Раннер миграций схемы на PRAGMA user_version.

Alembic намеренно не берётся: это инструмент экосистемы SQLAlchemy, его
autogenerate здесь не работает, а весь нужный функционал — «применить
недостающие шаги по порядку, каждый в своей транзакции» — умещается в этот
файл.

Запускается отдельным процессом до старта приложения
(`ExecStartPre=python -m server.migrate` в юнитах), а не из lifespan: миграция,
перестраивающая таблицу, не должна идти под живым трафиком. Само приложение
только сверяет версию и отказывается стартовать на отставшей базе — молча
обслуживать запросы по старой схеме хуже, чем не подняться.
"""

import os
import sqlite3
import sys
from contextlib import closing

from . import db
from .migrations import MIGRATIONS

TARGET = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _connect() -> sqlite3.Connection:
    """Отдельное соединение под миграции — не db.connect().

    isolation_level=None: транзакциями управляем сами, по одной на версию.
    С поведением драйвера по умолчанию явный BEGIN упирался бы в уже открытую
    неявную транзакцию.
    """
    conn = sqlite3.connect(db.DB_PATH, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    # Перестроение таблицы временно оставляет ссылки висящими; проверка ключей
    # в sqlite3 и так выключена по умолчанию, но полагаться на умолчание здесь
    # не стоит — цена ошибки слишком велика.
    conn.execute("PRAGMA foreign_keys=off")
    return conn


def run() -> list[int]:
    """Применяет недостающие миграции. Возвращает список применённых версий.

    Каждая версия идёт своей транзакцией. DDL в SQLite транзакционен, а
    `PRAGMA user_version` пишется в заголовок базы вместе с ней — значит
    упавшая миграция откатывается целиком и версию не двигает.
    """
    directory = os.path.dirname(db.DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    applied = []
    with closing(_connect()) as conn:
        for version, step in MIGRATIONS:
            if version <= current_version(conn):
                continue
            conn.execute("BEGIN")
            try:
                if callable(step):
                    step(conn)
                else:
                    for sql in step:
                        conn.execute(sql)
                conn.execute(f"PRAGMA user_version = {int(version)}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            applied.append(version)
    return applied


def assert_current() -> None:
    """Проверка версии на старте приложения. Расходится — не стартуем."""
    with closing(_connect()) as conn:
        version = current_version(conn)
    if version < TARGET:
        raise RuntimeError(
            f"схема БД версии {version}, приложению нужна {TARGET}."
            " Прогоните: python -m server.migrate")
    if version > TARGET:
        raise RuntimeError(
            f"схема БД версии {version} новее кода (он знает про {TARGET})."
            " Похоже, откатили приложение без отката базы")


def main() -> int:
    applied = run()
    if not applied:
        print(f"схема уже на версии {TARGET}")
        return 0
    print("применены миграции: " + ", ".join(str(v) for v in applied))
    # VACUUM только после реальных изменений и обязательно вне транзакции.
    # После выноса raw из measurements освобождается заметный кусок файла,
    # а без VACUUM он остаётся занятым.
    with closing(_connect()) as conn:
        conn.execute("VACUUM")
    print("VACUUM выполнен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
