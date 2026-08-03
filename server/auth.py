"""Учётные данные и проверки доступа.

Пока здесь только basic-auth датчика для push (креды лежат в /etc/tu4ka/env).
PUSH_USER/PUSH_PASS читаются в глобалы на импорте модуля — тесты подменяют
именно их, поэтому обращаться к ним нужно через модуль, а не через
`from .auth import PUSH_PASS`.
"""

import base64
import os
import secrets

from fastapi import HTTPException, Request

PUSH_USER = os.environ.get("TU4KA_PUSH_USER", "")
PUSH_PASS = os.environ.get("TU4KA_PUSH_PASS", "")


def check_push_auth(request: Request) -> None:
    if not PUSH_PASS:
        return
    header = request.headers.get("authorization", "")
    ok = False
    if header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
            ok = secrets.compare_digest(user, PUSH_USER) and secrets.compare_digest(pwd, PUSH_PASS)
        except Exception:
            ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="bad credentials",
                            headers={"WWW-Authenticate": "Basic"})
