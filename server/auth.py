"""Учётные данные и проверки доступа.

Пока здесь только basic-auth датчика для push. Источник правды по кредам —
таблица `devices`: логин (`push_user`) выбирает устройство, за которое
засчитывается измерение, пароль сверяется с `push_secret_sha256`. Переменные
окружения TU4KA_PUSH_USER/TU4KA_PUSH_PASS остались мостом на случай, когда
в БД секрета нет (см. `_env_bridge`), и их же читает миграция v2, заводя
первое устройство.

PUSH_USER/PUSH_PASS читаются в глобалы на импорте модуля — тесты подменяют
именно их, поэтому обращаться к ним нужно через модуль, а не через
`from .auth import PUSH_PASS`.
"""

import base64
import hashlib
import logging
import os
import secrets
import time
from contextlib import closing
from typing import NamedTuple

from fastapi import HTTPException

from . import db

PUSH_USER = os.environ.get("TU4KA_PUSH_USER", "")
PUSH_PASS = os.environ.get("TU4KA_PUSH_PASS", "")

# Хеш, с которым сверяется пароль, когда устройства с таким логином нет.
# Без него ответ на несуществующий логин приходил бы заметно раньше, чем
# на существующий, и перебором можно было бы вычислить живые логины.
_DUMMY_HASH = hashlib.sha256(b"tu4ka: no such device").hexdigest()

_DEVICE_COLUMNS = (
    "SELECT id, chip_id, chip_id_conflict_at, push_secret_sha256,"
    " push_secret_prev_sha256, push_secret_prev_until FROM devices"
)

# Расхождение БД и окружения — состояние, а не событие: без флага warning
# сыпался бы в журнал каждую минуту, с каждым push датчика.
_bridge_warned = False


class PushDevice(NamedTuple):
    """Устройство, за которое засчитывается push.

    chip_id и метка конфликта едут вместе с id, чтобы обработчик приёма
    обошёлся одним чтением devices на запрос.
    """

    id: int
    chip_id: str | None
    chip_id_conflict_at: int | None


def _unauthorized() -> None:
    raise HTTPException(status_code=401, detail="bad credentials",
                        headers={"WWW-Authenticate": "Basic"})


def _credentials(authorization: str) -> tuple[str, str] | None:
    """Пара (логин, пароль) из заголовка Basic. Любой мусор — None, не исключение."""
    if not authorization.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode()
    except Exception:
        return None
    user, sep, password = decoded.partition(":")
    if not sep:
        return None
    return user, password


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _secret_matches(row, provided: str) -> bool:
    """Сверка с текущим секретом, а при промахе — с прошлым, если он ещё жив.

    Grace-период нужен ротации токена: датчик перенастраивают руками, и без
    окна, где приняты оба секрета, перенастройка означала бы дыру в данных.
    """
    digest = _sha256(provided)
    current = row["push_secret_sha256"]
    if current is not None and secrets.compare_digest(digest, current):
        return True
    previous = row["push_secret_prev_sha256"]
    until = row["push_secret_prev_until"]
    if previous is not None and until is not None and time.time() < until:
        return secrets.compare_digest(digest, previous)
    return False


def _env_bridge(user: str, password: str) -> bool:
    """Сверка со старыми кредами из окружения.

    Мост на случай, когда в devices секрета нет, а в /etc/<среда>/env он есть
    (миграция отработала без окружения, пароль сменили руками после неё).
    Отказать здесь значило бы уронить доставку данных: прошивка не буферизует
    и не переспрашивает, а 401 — это безвозвратно потерянные измерения.
    Сравниваем байты: compare_digest на не-ASCII строках бросает TypeError.
    """
    global _bridge_warned
    ok = (secrets.compare_digest(user.encode(), PUSH_USER.encode())
          and secrets.compare_digest(password.encode(), PUSH_PASS.encode()))
    if ok and not _bridge_warned:
        _bridge_warned = True
        logging.getLogger("uvicorn.error").warning(
            "push принят по кредам из окружения: у устройства '%s' нет"
            " push_secret_sha256 в devices. БД и /etc/<среда>/env разошлись",
            user)
    return ok


def resolve_push_device(authorization: str) -> PushDevice:
    """Устройство по кредам push. Не сошлось — 401.

    Принимает строку заголовка, а не Request: так функция остаётся
    тестируемой без FastAPI и безопасной для вызова из отдельного треда
    (обработчик приёма зовёт её именно так — здесь поход в SQLite).
    """
    creds = _credentials(authorization)
    with closing(db.connect()) as conn:
        if creds is not None:
            user, password = creds
            row = conn.execute(
                _DEVICE_COLUMNS + " WHERE push_user = ?", (user,)).fetchone()
            if row is None:
                # Работа та же, что и при существующем логине, — иначе
                # время ответа выдаёт, какие логины заведены.
                secrets.compare_digest(_sha256(password), _DUMMY_HASH)
                _unauthorized()
            if _secret_matches(row, password):
                return _device(row)
            if row["push_secret_sha256"] is None:
                # Секрета в БД нет: либо приём вообще без авторизации
                # (пустой TU4KA_PUSH_PASS — тесты, свежая машина), либо мост.
                if not PUSH_PASS or _env_bridge(user, password):
                    return _device(row)
            _unauthorized()
        # Заголовка нет или он не разобрался. Это норма только пока секрета
        # нет нигде — сегодняшний режим «пароль не задан».
        if not PUSH_PASS and not _any_secret(conn):
            row = conn.execute(
                _DEVICE_COLUMNS + " ORDER BY id LIMIT 1").fetchone()
            if row is not None:
                return _device(row)
    _unauthorized()


def _device(row) -> PushDevice:
    return PushDevice(row["id"], row["chip_id"], row["chip_id_conflict_at"])


def _any_secret(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM devices WHERE push_secret_sha256 IS NOT NULL"
        " LIMIT 1").fetchone() is not None


def any_device_secret() -> bool:
    """Есть ли вообще секрет push в devices — для предупреждения на старте."""
    with closing(db.connect()) as conn:
        return _any_secret(conn)
