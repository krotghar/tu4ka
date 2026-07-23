"""tu4ka — приём и хранение данных датчика воздуха airRohr (SDS011 + BME280).

Принимает штатный push прошивки airRohr («отправка на собственный API»),
хранит историю в SQLite и отдаёт REST API + веб-морду.
"""

import base64
import json
import logging
import math
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, closing

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import aqi

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TU4KA_DB", "/var/lib/tu4ka/tu4ka.db")
PUSH_USER = os.environ.get("TU4KA_PUSH_USER", "")
PUSH_PASS = os.environ.get("TU4KA_PUSH_PASS", "")

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

# value_type прошивки -> колонка measurements
FIELD_MAP = {
    "SDS_P1": "pm10",
    "SDS_P2": "pm25",
    "BME280_temperature": "temperature",
    "BME280_humidity": "humidity",
    "BME280_pressure": "pressure",
    "BMP280_temperature": "temperature",
    "BMP280_pressure": "pressure",
    "temperature": "temperature",
    "humidity": "humidity",
    "pressure": "pressure",
    "signal": "signal",
}

PERIODS = {  # период истории -> (глубина, шаг усреднения), сек
    "day": (24 * 3600, 300),
    "week": (7 * 24 * 3600, 1800),
    "month": (30 * 24 * 3600, 7200),
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@asynccontextmanager
async def lifespan(_: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(db()) as conn, conn:
        conn.executescript(SCHEMA)
    if not PUSH_PASS:
        logging.getLogger("uvicorn.error").warning(
            "TU4KA_PUSH_PASS не задан — /api/v1/push принимает данные без авторизации")
    yield


app = FastAPI(
    title="tu4ka",
    description="Данные датчика воздуха airRohr: SDS011 (пыль) + BME280",
    version="1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")


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


MAX_PUSH_BODY = 64 * 1024  # реальный push прошивки ~500 байт


@app.post("/api/v1/push")
async def push(request: Request):
    """Приём измерения от прошивки airRohr (формат sensor.community)."""
    check_push_auth(request)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_PUSH_BODY:
            raise HTTPException(status_code=413, detail="body too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    values = data.get("sensordatavalues") if isinstance(data, dict) else None
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="sensordatavalues missing")

    row = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        col = FIELD_MAP.get(str(item.get("value_type", "")))
        if col is None or col in row:
            continue
        try:
            v = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        # inf/nan — валидные float, но ломают JSON-сериализацию ответов
        # и отравляют avg() в history
        if not math.isfinite(v):
            continue
        row[col] = v
    # прошивка отдаёт давление в Па — нормализуем в гПа
    if row.get("pressure") is not None and row["pressure"] > 2000:
        row["pressure"] = round(row["pressure"] / 100.0, 2)

    sensor_id = str(
        data.get("esp8266id")
        or data.get("esp32id")
        or request.headers.get("x-sensor")
        or ""
    )
    ts = int(time.time())

    def _insert():  # не блокируем event loop, если БД под локом
        with closing(db()) as conn, conn:
            conn.execute(
                "INSERT INTO measurements(ts, sensor_id, pm10, pm25, temperature,"
                " humidity, pressure, signal, raw) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts, sensor_id, row.get("pm10"), row.get("pm25"),
                 row.get("temperature"), row.get("humidity"), row.get("pressure"),
                 row.get("signal"), body.decode("utf-8", "replace")),
            )

    await anyio.to_thread.run_sync(_insert)
    return {"ok": True, "ts": ts}


def _nowcast_aqi(conn, now):
    """AQI по методу EPA NowCast: почасовые средние PM за 12 ч, взвешенно."""
    cur_hour = now // 3600
    rows = conn.execute(
        "SELECT (ts/3600) AS h, avg(pm25) AS pm25, avg(pm10) AS pm10"
        " FROM measurements WHERE ts >= ? GROUP BY h",
        (now - 12 * 3600,),
    ).fetchall()
    pm25_by_ago, pm10_by_ago = {}, {}
    for row in rows:
        ago = cur_hour - row["h"]
        if row["pm25"] is not None:
            pm25_by_ago[ago] = row["pm25"]
        if row["pm10"] is not None:
            pm10_by_ago[ago] = row["pm10"]
    result = aqi.compute(aqi.nowcast(pm25_by_ago), aqi.nowcast(pm10_by_ago))
    result["method"] = "nowcast_12h"
    return result


@app.get("/api/v1/current")
def current():
    """Последнее измерение, его возраст и текущий AQI (US EPA, NowCast)."""
    now = int(time.time())
    with closing(db()) as conn:
        r = conn.execute(
            "SELECT ts, sensor_id, pm10, pm25, temperature, humidity,"
            " pressure, signal FROM measurements ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        if r is None:
            return {"ts": None}
        aqi_info = _nowcast_aqi(conn, now)
    d = dict(r)
    d["age_s"] = max(0, now - d["ts"])
    d["aqi"] = aqi_info
    return d


@app.get("/api/v1/history")
def history(period: str = "day"):
    """История, усреднённая по корзинам: day (5 мин), week (30 мин), month (2 ч)."""
    if period not in PERIODS:
        raise HTTPException(status_code=400,
                            detail=f"period must be one of {sorted(PERIODS)}")
    span, bucket = PERIODS[period]
    since = int(time.time()) - span
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT (ts/?)*? AS t, round(avg(pm25),2) AS pm25,"
            " round(avg(pm10),2) AS pm10, round(avg(temperature),2) AS temperature,"
            " round(avg(humidity),2) AS humidity, round(avg(pressure),2) AS pressure,"
            " count(*) AS n FROM measurements WHERE ts >= ? GROUP BY t ORDER BY t",
            (bucket, bucket, since),
        ).fetchall()
    points = []
    for r in rows:
        p = dict(r)
        p["aqi"] = aqi.compute(p["pm25"], p["pm10"])["aqi"]  # мгновенный AQI корзины
        points.append(p)
    return {"period": period, "bucket_s": bucket, "points": points}


@app.get("/healthz", include_in_schema=False)
def healthz():
    with closing(db()) as conn:
        r = conn.execute("SELECT max(ts) AS ts, count(*) AS n FROM measurements").fetchone()
    return {"ok": True, "last_ts": r["ts"], "rows": r["n"]}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))
