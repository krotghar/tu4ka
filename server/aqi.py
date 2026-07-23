"""Расчёт индекса качества воздуха (US EPA AQI).

Источник истины — AirNow «Technical Assistance Document for the Reporting of
Daily Air Quality», Table 6 (редакция от мая 2024, breakpoints PM2.5 обновлены).
NowCast — 12-часовое взвешенное среднее, штатный метод EPA для отчёта AQI по
данным датчиков в реальном времени.

Чистые функции без зависимостей — переиспользуются будущим REST-клиентом и ботом.
"""

import math

# (concentration_low, concentration_high, aqi_low, aqi_high)
# PM2.5 — µg/m³, 24ч; редакция 2024. Верхняя строка обрезана на AQI 500 (C=325.4).
PM25_BP = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]
# PM10 — µg/m³, 24ч. Верхняя строка обрезана на AQI 500 (C=604).
PM10_BP = [
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 604, 301, 500),
]

# aqi_high границы категорий -> стабильный ключ (маппинг подписей/цветов — на клиенте)
CATEGORIES = [
    (50, "good"),
    (100, "moderate"),
    (150, "usg"),
    (200, "unhealthy"),
    (300, "very_unhealthy"),
    (500, "hazardous"),
]


def category(aqi):
    """AQI (int) -> ключ категории EPA."""
    if aqi is None:
        return None
    for limit, key in CATEGORIES:
        if aqi <= limit:
            return key
    return "hazardous"


def _truncate(conc, step):
    """Обрезка концентрации до сетки breakpoints (EPA: PM2.5 → 0.1, PM10 → 1).

    Финальный round(..., 10) гасит бинарную float-погрешность умножения: без него
    554 * 0.1 == 55.400000000000006 > 55.4, значение уезжает за верхнюю границу
    категории и AQI считается на строку выше (55.4 → 151 вместо 150).
    """
    return round(math.floor(round(conc / step, 6)) * step, 10)


def sub_index(conc, breakpoints, step):
    """Sub-index одного загрязнителя по таблице breakpoints (уравнение 1 EPA)."""
    if conc is None:
        return None
    c = max(0.0, _truncate(conc, step))
    for clo, chi, ilo, ihi in breakpoints:
        if c <= chi:
            return round((ihi - ilo) / (chi - clo) * (c - clo) + ilo)
    # выше верхнего breakpoint — экстраполируем по последней строке, но не выше 500
    clo, chi, ilo, ihi = breakpoints[-1]
    return min(500, round((ihi - ilo) / (chi - clo) * (c - clo) + ilo))


def sub_index_pm25(conc):
    return sub_index(conc, PM25_BP, 0.1)


def sub_index_pm10(conc):
    return sub_index(conc, PM10_BP, 1)


def compute(pm25, pm10):
    """Итоговый AQI = max sub-index'ов; возвращает dict или None-поля.

    dominant — загрязнитель с наибольшим sub-index (тот, что «определяет» AQI).
    """
    i25 = sub_index_pm25(pm25)
    i10 = sub_index_pm10(pm10)
    parts = [(i, name) for i, name in ((i25, "pm25"), (i10, "pm10")) if i is not None]
    if not parts:
        return {"aqi": None, "category": None, "dominant": None,
                "pm25_aqi": None, "pm10_aqi": None}
    aqi, dominant = max(parts, key=lambda p: p[0])
    return {
        "aqi": aqi,
        "category": category(aqi),
        "dominant": dominant,
        "pm25_aqi": i25,
        "pm10_aqi": i10,
    }


def nowcast(values_by_hours_ago):
    """NowCast-концентрация из почасовых значений за последние 12 ч.

    values_by_hours_ago: {hours_ago: concentration}, hours_ago 0..11 (0 = текущий час).
    Возвращает float или None, если данных нет.
    """
    vals = {h: c for h, c in values_by_hours_ago.items()
            if c is not None and 0 <= h <= 11}
    if not vals:
        return None
    present = list(vals.values())
    cmax = max(present)
    if cmax <= 0:
        return 0.0
    weight = 1.0 - (cmax - min(present)) / cmax
    weight = min(1.0, max(0.5, weight))  # диапазон [0.5, 1]
    num = sum(c * weight ** h for h, c in vals.items())
    den = sum(weight ** h for h in vals)
    return num / den if den else None


if __name__ == "__main__":
    # Самотест по эталонным точкам EPA (Table 6, 2024) + регрессия на 55.4.
    # Запуск: python3 aqi.py
    cases = [
        (sub_index_pm25(9.0), 50),
        (sub_index_pm25(12.0), 56),
        (sub_index_pm25(35.4), 100),
        (sub_index_pm25(55.4), 150),   # регрессия: float-обрезка не должна дать 151
        (sub_index_pm25(55.45), 150),
        (sub_index_pm25(55.5), 151),
        (sub_index_pm25(0.0), 0),
        (sub_index_pm25(400.0), 500),  # выше верхнего breakpoint — кап на 500
        (sub_index_pm10(54), 50),
        (sub_index_pm10(155), 101),
        (sub_index_pm10(425), 301),
        (compute(55.4, None)["category"], "usg"),
        (compute(50.0, 40.0)["dominant"], "pm25"),
        (category(75), "moderate"),
        (round(nowcast({0: 20, 1: 10}), 2), 16.67),
        (nowcast({}), None),
    ]
    bad = [(i, got, exp) for i, (got, exp) in enumerate(cases) if got != exp]
    for i, got, exp in bad:
        print(f"FAIL case {i}: got {got!r}, expected {exp!r}")
    print(f"{len(bad)} FAILED" if bad else f"OK, all {len(cases)} cases passed")
    raise SystemExit(1 if bad else 0)
