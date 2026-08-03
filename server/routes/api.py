"""REST API: текущее измерение, история, проверка живости."""

import time
from contextlib import closing

from fastapi import APIRouter, HTTPException

from .. import aqi, db
# Имена, а не модуль: обработчик ниже называется history() — под этим именем он
# попадает в operationId OpenAPI, так что переименовывать его ради импорта модуля
# нельзя. PERIODS/MAX_TZ_OFFSET — константы, build_history — чистая функция;
# в отличие от db.DB_PATH и auth.PUSH_* их никто не подменяет в тестах.
from ..history import MAX_TZ_OFFSET, PERIODS, build_history

router = APIRouter()


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


@router.get("/api/v1/current")
def current():
    """Последнее измерение, его возраст и текущий AQI (US EPA, NowCast)."""
    now = int(time.time())
    with closing(db.connect()) as conn:
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


@router.get("/api/v1/history")
def history(period: str = "24h", tz_offset: int = 0):
    """История за скользящее окно: 24h / 7d / 30d / 12m / all.

    Усреднение по корзинам: 24h (1 ч), 7d (3 ч), 30d (1 сутки), 12m (2 суток),
    all (15 суток). Корзины выровнены по фиксированной сетке (см. period_window),
    а не по границе окна — иначе сетка съезжала бы на каждый запрос вместе с `now`.
    Сетка плотная: корзины без измерений отдаются с null и n=0, чтобы график
    занимал окно целиком, даже когда данных за его начало нет.

    aqi — мгновенный AQI по средним PM корзины; aqi_lo/aqi_hi — по минимуму и
    максимуму PM внутри корзины (диапазон для ленты на графике). aqi_hi точен
    (sub-index монотонен по концентрации), aqi_lo — нижняя оценка: реальный
    минимум max(pm25_aqi, pm10_aqi) внутри корзины может быть чуть выше.

    <метрика>_lo/_hi (pm25, pm10, temperature, humidity, pressure) — минимум и
    максимум самой метрики внутри корзины, для ленты разброса на графике при
    выборе этой метрики.
    """
    if period not in PERIODS:
        raise HTTPException(status_code=400,
                            detail=f"period must be one of {sorted(PERIODS)}")
    if not -MAX_TZ_OFFSET <= tz_offset <= MAX_TZ_OFFSET:
        raise HTTPException(
            status_code=400,
            detail=f"tz_offset must be within ±{MAX_TZ_OFFSET} minutes")
    now = int(time.time())
    with closing(db.connect()) as conn:
        return build_history(conn, period, tz_offset, now)


@router.get("/healthz", include_in_schema=False)
def healthz():
    with closing(db.connect()) as conn:
        r = conn.execute("SELECT max(ts) AS ts, count(*) AS n FROM measurements").fetchone()
    return {"ok": True, "last_ts": r["ts"], "rows": r["n"]}
