"""История измерений: скользящие окна, сетка корзин, агрегация.

Без зависимости от FastAPI — валидация параметров и HTTP-ошибки живут в роуте
(server/routes/api.py), сюда приходят уже проверенные значения.
"""

from . import aqi

PERIODS = {  # период истории -> (длина скользящего окна, сек; None = вся история; шаг корзины, сек)
    "24h": (24 * 3600, 3600),
    "7d": (7 * 86400, 3 * 3600),
    "30d": (30 * 86400, 86400),
    "12m": (364 * 86400, 2 * 86400),
    "all": (None, 15 * 86400),
}
MAX_TZ_OFFSET = 14 * 60  # минут; реальные зоны укладываются в UTC-12..UTC+14


def period_window(now: int, period: str, tz_offset: int, first_ts: int | None) -> tuple[int, int]:
    """Границы скользящего окна, выровненные по сетке корзины.

    tz_offset — часовой пояс клиента: минут к востоку от UTC, то есть
    -Date.prototype.getTimezoneOffset() браузера. Сетка корзин сдвинута на
    tz_offset, чтобы суточные и более крупные корзины (30d, 12m, all) стояли
    на местных полуночах, а не на границах UTC-суток.

    end — начало корзины, в которую попадает `now` (эта корзина обычно ещё не
    заполнена целиком). Для окон фиксированной длины start = end - span; для
    "all" — начало корзины, в которую попадает первое измерение (first_ts),
    либо end, если измерений ещё нет.
    """
    span, bucket = PERIODS[period]
    shift = tz_offset * 60
    end = ((now + shift) // bucket) * bucket - shift
    if span is not None:
        start = end - span
    elif first_ts is None:
        start = end
    else:
        start = ((first_ts + shift) // bucket) * bucket - shift
    return start, end


def build_history(conn, period: str, tz_offset: int, now: int) -> dict:
    """Тело ответа /api/v1/history: точки по корзинам скользящего окна.

    Корзины выровнены по фиксированной сетке (см. period_window), а не по границе
    окна — иначе сетка съезжала бы на каждый запрос вместе с `now`. Сетка плотная:
    корзины без измерений отдаются с null и n=0, чтобы график занимал окно целиком.
    Контракт полей ответа (aqi/aqi_lo/aqi_hi, <метрика>_lo/_hi) описан в докстринге
    обработчика — он же уходит в OpenAPI.
    """
    bucket = PERIODS[period][1]
    first_ts = conn.execute("SELECT min(ts) AS ts FROM measurements").fetchone()["ts"]
    start, end = period_window(now, period, tz_offset, first_ts)
    rows = conn.execute(
        "SELECT ?+((ts-?)/?)*? AS t, round(avg(pm25),2) AS pm25,"
        " round(avg(pm10),2) AS pm10, round(avg(temperature),2) AS temperature,"
        " round(avg(humidity),2) AS humidity, round(avg(pressure),2) AS pressure,"
        " round(min(pm25),2) AS pm25_lo, round(max(pm25),2) AS pm25_hi,"
        " round(min(pm10),2) AS pm10_lo, round(max(pm10),2) AS pm10_hi,"
        " round(min(temperature),2) AS temperature_lo, round(max(temperature),2) AS temperature_hi,"
        " round(min(humidity),2) AS humidity_lo, round(max(humidity),2) AS humidity_hi,"
        " round(min(pressure),2) AS pressure_lo, round(max(pressure),2) AS pressure_hi,"
        " count(*) AS n FROM measurements WHERE ts >= ? GROUP BY t ORDER BY t",
        (start, start, bucket, bucket, start),
    ).fetchall()
    by_t = {r["t"]: dict(r) for r in rows}
    points = []
    for t in range(start, end + 1, bucket):
        p = by_t.get(t) or {
            "t": t, "pm25": None, "pm10": None, "temperature": None,
            "humidity": None, "pressure": None, "n": 0,
            "pm25_lo": None, "pm25_hi": None, "pm10_lo": None, "pm10_hi": None,
            "temperature_lo": None, "temperature_hi": None,
            "humidity_lo": None, "humidity_hi": None,
            "pressure_lo": None, "pressure_hi": None,
        }
        p["aqi"] = aqi.compute(p["pm25"], p["pm10"])["aqi"]  # мгновенный AQI корзины
        p["aqi_lo"] = aqi.compute(p["pm25_lo"], p["pm10_lo"])["aqi"]
        p["aqi_hi"] = aqi.compute(p["pm25_hi"], p["pm10_hi"])["aqi"]
        points.append(p)
    return {"period": period, "bucket_s": bucket, "start": start, "end": now,
            "first_ts": first_ts, "points": points}
