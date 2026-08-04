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

# Пока устройство одно и креды общие. Маршрутизация по устройству из кредов —
# следующая сессия; здесь константа, чтобы схема с device_id NOT NULL работала,
# а поведение приёма не изменилось ни на байт.
DEVICE_ID = 1


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
            # ON CONFLICT, а не голый INSERT: idx_meas_dev_ts уникален по
            # (device_id, ts), и второй пуш в ту же секунду иначе вернул бы
            # прошивке 500. Она не переспрашивает — пусть поздний пуш
            # перезапишет ранний, чем потеряется вместе с ответом.
            cur = conn.execute(
                "INSERT INTO measurements(device_id, ts, sensor_id, pm10, pm25,"
                " temperature, humidity, pressure, signal) VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(device_id, ts) DO UPDATE SET"
                " sensor_id=excluded.sensor_id, pm10=excluded.pm10,"
                " pm25=excluded.pm25, temperature=excluded.temperature,"
                " humidity=excluded.humidity, pressure=excluded.pressure,"
                " signal=excluded.signal RETURNING id",
                (DEVICE_ID, ts, sensor_id, row.get("pm10"), row.get("pm25"),
                 row.get("temperature"), row.get("humidity"), row.get("pressure"),
                 row.get("signal")),
            )
            measurement_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO measurements_raw(measurement_id, raw) VALUES(?,?)"
                " ON CONFLICT(measurement_id) DO UPDATE SET raw=excluded.raw",
                (measurement_id, body.decode("utf-8", "replace")),
            )

    await anyio.to_thread.run_sync(_insert)
    return {"ok": True, "ts": ts}
