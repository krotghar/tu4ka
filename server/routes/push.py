"""Приём измерений от прошивки airRohr (формат sensor.community)."""

import json
import math
import time
from contextlib import closing

import anyio
from fastapi import APIRouter, HTTPException, Request

from .. import auth, db

router = APIRouter()

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

MAX_PUSH_BODY = 64 * 1024  # реальный push прошивки ~500 байт


@router.post("/api/v1/push")
async def push(request: Request):
    """Приём измерения от прошивки airRohr (формат sensor.community)."""
    auth.check_push_auth(request)
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
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO measurements(ts, sensor_id, pm10, pm25, temperature,"
                " humidity, pressure, signal, raw) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts, sensor_id, row.get("pm10"), row.get("pm25"),
                 row.get("temperature"), row.get("humidity"), row.get("pressure"),
                 row.get("signal"), body.decode("utf-8", "replace")),
            )

    await anyio.to_thread.run_sync(_insert)
    return {"ok": True, "ts": ts}
