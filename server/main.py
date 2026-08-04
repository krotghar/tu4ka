"""tu4ka — приём и хранение данных датчика воздуха airRohr (SDS011 + BME280).

Принимает штатный push прошивки airRohr («отправка на собственный API»),
хранит историю в SQLite и отдаёт REST API + веб-морду.

Здесь только сборка приложения: сам код разнесён по модулям пакета —
db.py (SQLite), migrate.py/migrations.py (версии схемы), auth.py (креды push),
history.py (окна и корзины), aqi.py (US EPA AQI), routes/ (эндпоинты).
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, migrate
from .routes import api as api_routes
from .routes import public as public_routes
from .routes import push as push_routes

APP_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Схему приложение не создаёт и не мигрирует: это делает отдельный процесс
    # до старта (ExecStartPre=python -m server.migrate). Здесь только сверка —
    # обслуживать запросы по отставшей схеме хуже, чем не подняться.
    migrate.assert_current()
    # Предупреждаем, только если секрета нет нигде: после перехода на креды
    # устройства нормальное состояние — пустой TU4KA_PUSH_PASS и хеши в devices,
    # и ругаться на него значило бы врать в журнал.
    if not auth.PUSH_PASS and not auth.any_device_secret():
        logging.getLogger("uvicorn.error").warning(
            "секрета push нет ни в devices, ни в TU4KA_PUSH_PASS —"
            " /api/v1/push принимает данные без авторизации")
    yield


app = FastAPI(
    title="tu4ka",
    description="Данные датчика воздуха airRohr: SDS011 (пыль) + BME280",
    version="1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")

app.include_router(push_routes.router)
app.include_router(api_routes.router)
app.include_router(public_routes.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))
