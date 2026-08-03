"""Юнит-тесты чистого модуля aqi.py — US EPA AQI и NowCast.

Источник ожидания помечен у каждой группы кейсов:
  «таблица EPA» — AirNow Technical Assistance Document, Table 6 (редакция 2024),
                  тот же первоисточник, что и для breakpoints в самом модуле;
  «регрессия»   — защита от бинарной погрешности float в _truncate;
  «инвариант»   — свойство, обязанное выполняться на любом входе.

Модуль без зависимостей, поэтому фикстуры из conftest.py (client, db_path и пр.)
здесь не нужны — функции вызываются напрямую.

Идентификаторы кейсов латиницей намеренно: pytest эскейпит не-ASCII в ids
до вида \\u0434\\u043e..., и в выводе они становятся нечитаемыми.
"""

import math

import pytest

from server import aqi


# --------------------------------------------------------------------------
# Порт самотеста из блока `if __name__ == "__main__"` в конце aqi.py.
# Кейсы заведены лямбдами, чтобы вычисляться в момент прогона теста, а не на
# стадии сборки параметров: иначе исключение внутри aqi уронило бы коллекцию.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("call,expected", [
    pytest.param(lambda: aqi.sub_index_pm25(9.0), 50, id="selftest-pm25-9.0"),
    pytest.param(lambda: aqi.sub_index_pm25(12.0), 56, id="selftest-pm25-12.0"),
    pytest.param(lambda: aqi.sub_index_pm25(35.4), 100, id="selftest-pm25-35.4"),
    # регрессия: float-обрезка не должна дать 151
    pytest.param(lambda: aqi.sub_index_pm25(55.4), 150, id="selftest-pm25-55.4"),
    pytest.param(lambda: aqi.sub_index_pm25(55.45), 150, id="selftest-pm25-55.45"),
    pytest.param(lambda: aqi.sub_index_pm25(55.5), 151, id="selftest-pm25-55.5"),
    pytest.param(lambda: aqi.sub_index_pm25(0.0), 0, id="selftest-pm25-0.0"),
    # выше верхнего breakpoint — кап на 500
    pytest.param(lambda: aqi.sub_index_pm25(400.0), 500, id="selftest-pm25-400.0"),
    pytest.param(lambda: aqi.sub_index_pm10(54), 50, id="selftest-pm10-54"),
    pytest.param(lambda: aqi.sub_index_pm10(155), 101, id="selftest-pm10-155"),
    pytest.param(lambda: aqi.sub_index_pm10(425), 301, id="selftest-pm10-425"),
    pytest.param(lambda: aqi.compute(55.4, None)["category"], "usg",
                 id="selftest-compute-category"),
    pytest.param(lambda: aqi.compute(50.0, 40.0)["dominant"], "pm25",
                 id="selftest-compute-dominant"),
    pytest.param(lambda: aqi.category(75), "moderate", id="selftest-category-75"),
    pytest.param(lambda: round(aqi.nowcast({0: 20, 1: 10}), 2), 16.67,
                 id="selftest-nowcast-16.67"),
    pytest.param(lambda: aqi.nowcast({}), None, id="selftest-nowcast-empty"),
])
def test_selftest_cases_from_module(call, expected):
    """Эталонные точки из самотеста aqi.py, перенесённые в pytest."""
    assert call() == expected


# --------------------------------------------------------------------------
# Граничные точки таблицы EPA
# --------------------------------------------------------------------------
@pytest.mark.parametrize("conc,expected", [
    # таблица EPA: нижняя граница строки -> ilo, верхняя -> ihi
    pytest.param(0.0, 0, id="pm25-0.0-good-lo"),
    pytest.param(9.0, 50, id="pm25-9.0-good-hi"),
    pytest.param(9.1, 51, id="pm25-9.1-moderate-lo"),
    pytest.param(12.0, 56, id="pm25-12.0-moderate-mid"),
    pytest.param(35.4, 100, id="pm25-35.4-moderate-hi"),
    pytest.param(35.5, 101, id="pm25-35.5-usg-lo"),
    pytest.param(55.4, 150, id="pm25-55.4-usg-hi"),
    pytest.param(55.45, 150, id="pm25-55.45-truncated-to-55.4"),
    pytest.param(55.5, 151, id="pm25-55.5-unhealthy-lo"),
    pytest.param(125.4, 200, id="pm25-125.4-unhealthy-hi"),
    pytest.param(125.5, 201, id="pm25-125.5-very-unhealthy-lo"),
    pytest.param(225.4, 300, id="pm25-225.4-very-unhealthy-hi"),
    pytest.param(225.5, 301, id="pm25-225.5-hazardous-lo"),
    pytest.param(325.4, 500, id="pm25-325.4-hazardous-hi"),
])
def test_sub_index_pm25_epa_breakpoints(conc, expected):
    """Таблица EPA: границы строк PM2.5 дают ровно ilo/ihi своей строки."""
    assert aqi.sub_index_pm25(conc) == expected


@pytest.mark.parametrize("conc,expected", [
    pytest.param(0, 0, id="pm10-0-good-lo"),
    pytest.param(54, 50, id="pm10-54-good-hi"),
    pytest.param(55, 51, id="pm10-55-moderate-lo"),
    pytest.param(154, 100, id="pm10-154-moderate-hi"),
    pytest.param(155, 101, id="pm10-155-usg-lo"),
    pytest.param(254, 150, id="pm10-254-usg-hi"),
    pytest.param(255, 151, id="pm10-255-unhealthy-lo"),
    pytest.param(354, 200, id="pm10-354-unhealthy-hi"),
    pytest.param(355, 201, id="pm10-355-very-unhealthy-lo"),
    pytest.param(424, 300, id="pm10-424-very-unhealthy-hi"),
    pytest.param(425, 301, id="pm10-425-hazardous-lo"),
    pytest.param(604, 500, id="pm10-604-hazardous-hi"),
])
def test_sub_index_pm10_epa_breakpoints(conc, expected):
    """Таблица EPA: границы строк PM10 дают ровно ilo/ihi своей строки."""
    assert aqi.sub_index_pm10(conc) == expected


# --------------------------------------------------------------------------
# Регрессия: бинарная погрешность float в _truncate
# --------------------------------------------------------------------------
def _naive_sub_index_pm25(conc):
    """Та же формула, что в aqi.sub_index_pm25, но с наивной обрезкой —
    без защитных round'ов из _truncate. Существует ради теста ниже: он должен
    сравнивать две живые реализации, а не константу с константой."""
    c = max(0.0, math.floor(conc / 0.1) * 0.1)
    for clo, chi, ilo, ihi in aqi.PM25_BP:
        if c <= chi:
            return round((ihi - ilo) / (chi - clo) * (c - clo) + ilo)
    return None


@pytest.mark.parametrize("conc,expected,naive", [
    # Ошибка обратного умножения: 554 * 0.1 == 55.400000000000006 > 55.4,
    # без round(..., 10) значение уезжает в следующую строку таблицы.
    pytest.param(55.4, 150, 151, id="regression-mul-55.4"),
    # Ошибка деления: floor(9.1 / 0.1) == 90 вместо 91, без round(..., 6)
    # концентрация обрезается на 0.1 вниз и AQI выходит на единицу меньше.
    pytest.param(9.1, 51, 50, id="regression-div-9.1"),
    pytest.param(0.3, 2, 1, id="regression-div-0.3"),
    pytest.param(0.7, 4, 3, id="regression-div-0.7"),
    pytest.param(12.1, 57, 56, id="regression-div-12.1"),
])
def test_sub_index_pm25_float_truncation_regression(conc, expected, naive):
    """Обрезка на сетку 0.1 не должна страдать от бинарной погрешности float.

    `expected` — значение по таблице EPA, `naive` — то, что возвращает наивная
    реализация без защитных round'ов. Оба числа проверяются на живом коде:
    иначе колонка `naive` со временем превратилась бы в комментарий-враньё.
    """
    assert _naive_sub_index_pm25(conc) == naive, "кейс перестал различать реализации"
    assert aqi.sub_index_pm25(conc) == expected


# --------------------------------------------------------------------------
# None на входе
# --------------------------------------------------------------------------
@pytest.mark.parametrize("func", [
    pytest.param(aqi.sub_index_pm25, id="sub_index_pm25"),
    pytest.param(aqi.sub_index_pm10, id="sub_index_pm10"),
    pytest.param(aqi.category, id="category"),
])
def test_none_in_none_out(func):
    """Нет данных -> нет результата: None пробрасывается, а не падает."""
    assert func(None) is None


def test_sub_index_none_with_explicit_breakpoints():
    """sub_index отдаёт None до обращения к таблице, независимо от неё."""
    assert aqi.sub_index(None, aqi.PM25_BP, 0.1) is None
    assert aqi.sub_index(None, aqi.PM10_BP, 1) is None


# --------------------------------------------------------------------------
# Отрицательная концентрация зажимается в 0 (max(0.0, ...) в sub_index)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("conc", [
    pytest.param(-0.05, id="pm25-neg-0.05"),
    pytest.param(-0.1, id="pm25-neg-0.1"),
    pytest.param(-5.0, id="pm25-neg-5.0"),
    pytest.param(-1000.0, id="pm25-neg-1000.0"),
])
def test_sub_index_pm25_clamps_negative_to_zero(conc):
    """Отрицательная концентрация физически невозможна — зажимаем в 0, не в минус."""
    assert aqi.sub_index_pm25(conc) == 0


@pytest.mark.parametrize("conc", [
    pytest.param(-1, id="pm10-neg-1"),
    pytest.param(-100, id="pm10-neg-100"),
])
def test_sub_index_pm10_clamps_negative_to_zero(conc):
    """То же для PM10: ниже нуля sub-index не опускается."""
    assert aqi.sub_index_pm10(conc) == 0


# --------------------------------------------------------------------------
# Кап сверху: ровно 500, не выше
# --------------------------------------------------------------------------
@pytest.mark.parametrize("conc", [
    pytest.param(325.5, id="pm25-325.5-just-past-table"),
    pytest.param(400.0, id="pm25-400.0"),
    pytest.param(1000.0, id="pm25-1000.0"),
    pytest.param(100000.0, id="pm25-100000.0"),
])
def test_sub_index_pm25_capped_at_500(conc):
    """Выше верхнего breakpoint экстраполяция обрезается ровно на 500."""
    assert aqi.sub_index_pm25(conc) == 500


@pytest.mark.parametrize("conc", [
    pytest.param(605, id="pm10-605-just-past-table"),
    pytest.param(700, id="pm10-700"),
    pytest.param(10000, id="pm10-10000"),
])
def test_sub_index_pm10_capped_at_500(conc):
    """То же для PM10."""
    assert aqi.sub_index_pm10(conc) == 500


def test_sub_index_never_exceeds_500():
    """Инвариант: sub-index не вылезает за шкалу AQI ни на какой концентрации."""
    for conc in [round(i * 0.5, 10) for i in range(0, 4000)]:
        assert 0 <= aqi.sub_index_pm25(conc) <= 500, f"pm25 вне шкалы при conc={conc}"
    for conc in range(0, 2000):
        assert 0 <= aqi.sub_index_pm10(conc) <= 500, f"pm10 вне шкалы при conc={conc}"


# --------------------------------------------------------------------------
# category
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    pytest.param(0, "good", id="category-0"),
    pytest.param(50, "good", id="category-50-good-hi"),
    pytest.param(51, "moderate", id="category-51-moderate-lo"),
    pytest.param(100, "moderate", id="category-100-moderate-hi"),
    pytest.param(101, "usg", id="category-101-usg-lo"),
    pytest.param(150, "usg", id="category-150-usg-hi"),
    pytest.param(151, "unhealthy", id="category-151-unhealthy-lo"),
    pytest.param(200, "unhealthy", id="category-200-unhealthy-hi"),
    pytest.param(201, "very_unhealthy", id="category-201-very-unhealthy-lo"),
    pytest.param(300, "very_unhealthy", id="category-300-very-unhealthy-hi"),
    pytest.param(301, "hazardous", id="category-301-hazardous-lo"),
    pytest.param(500, "hazardous", id="category-500-scale-top"),
    pytest.param(501, "hazardous", id="category-501-above-scale"),
    pytest.param(9999, "hazardous", id="category-9999-above-scale"),
])
def test_category_boundaries(value, expected):
    """Таблица EPA: границы категорий 50/100/150/200/300/500 и всё, что выше."""
    assert aqi.category(value) == expected


def test_category_covers_every_declared_key():
    """Инвариант: каждая строка CATEGORIES достижима по своей верхней границе."""
    keys = {key for _, key in aqi.CATEGORIES}
    assert len(keys) == len(aqi.CATEGORIES), "ключи категорий не уникальны"
    for limit, key in aqi.CATEGORIES:
        assert aqi.category(limit) == key
    assert keys == {"good", "moderate", "usg", "unhealthy",
                    "very_unhealthy", "hazardous"}


# --------------------------------------------------------------------------
# compute
# --------------------------------------------------------------------------
@pytest.mark.parametrize("pm25,pm10,expected", [
    pytest.param(50.0, 40.0, {"aqi": 137, "category": "usg", "dominant": "pm25",
                              "pm25_aqi": 137, "pm10_aqi": 37},
                 id="compute-pm25-dominates"),
    pytest.param(5.0, 200, {"aqi": 123, "category": "usg", "dominant": "pm10",
                            "pm25_aqi": 28, "pm10_aqi": 123},
                 id="compute-pm10-dominates"),
    pytest.param(None, None, {"aqi": None, "category": None, "dominant": None,
                              "pm25_aqi": None, "pm10_aqi": None},
                 id="compute-no-data"),
    pytest.param(55.4, None, {"aqi": 150, "category": "usg", "dominant": "pm25",
                              "pm25_aqi": 150, "pm10_aqi": None},
                 id="compute-pm25-only"),
    pytest.param(None, 155, {"aqi": 101, "category": "usg", "dominant": "pm10",
                             "pm25_aqi": None, "pm10_aqi": 101},
                 id="compute-pm10-only"),
    # Ничья: max() берёт первый максимум, а первым в списке идёт pm25.
    # Поведение зафиксировано намеренно, чтобы dominant не «мигал» на равных значениях.
    pytest.param(9.0, 54, {"aqi": 50, "category": "good", "dominant": "pm25",
                           "pm25_aqi": 50, "pm10_aqi": 50},
                 id="compute-tie-pm25-wins"),
])
def test_compute(pm25, pm10, expected):
    """Таблица EPA: итоговый AQI, доминирующий загрязнитель и оба sub-index'а."""
    assert aqi.compute(pm25, pm10) == expected


def test_compute_returns_fixed_key_set():
    """Контракт для клиентов (/current, бот): набор ключей не зависит от входа."""
    keys = {"aqi", "category", "dominant", "pm25_aqi", "pm10_aqi"}
    assert set(aqi.compute(50.0, 40.0)) == keys
    assert set(aqi.compute(None, None)) == keys


@pytest.mark.parametrize("pm25,pm10", [
    pytest.param(50.0, 40.0, id="invariant-pm25-higher"),
    pytest.param(20.0, 150.0, id="invariant-pm10-higher"),
    pytest.param(100.0, 100.0, id="invariant-both-high"),
    pytest.param(5.0, 5.0, id="invariant-both-low"),
    pytest.param(0.0, 0, id="invariant-zeros"),
    pytest.param(400.0, 700, id="invariant-both-off-scale"),
    pytest.param(55.4, None, id="invariant-pm25-only"),
    pytest.param(None, 155, id="invariant-pm10-only"),
])
def test_compute_aqi_is_max_of_sub_indexes(pm25, pm10):
    """Инвариант: aqi == max доступных sub-index'ов, dominant указывает на него,
    а category согласована с aqi. Числа не зашиты — сверяемся с самими функциями."""
    result = aqi.compute(pm25, pm10)
    parts = {"pm25": aqi.sub_index_pm25(pm25), "pm10": aqi.sub_index_pm10(pm10)}
    present = {name: value for name, value in parts.items() if value is not None}

    assert result["pm25_aqi"] == parts["pm25"]
    assert result["pm10_aqi"] == parts["pm10"]
    assert result["aqi"] == max(present.values())
    assert result["dominant"] in present
    assert present[result["dominant"]] == result["aqi"]
    assert result["category"] == aqi.category(result["aqi"])


# --------------------------------------------------------------------------
# nowcast
# --------------------------------------------------------------------------
@pytest.mark.parametrize("values", [
    pytest.param({}, id="nowcast-empty-dict"),
    pytest.param({0: None}, id="nowcast-single-none"),
    pytest.param({0: None, 3: None}, id="nowcast-all-none"),
    pytest.param({12: 100, 13: 5}, id="nowcast-all-hours-outside-window"),
    pytest.param({-1: 100}, id="nowcast-negative-hour"),
])
def test_nowcast_returns_none_without_usable_data(values):
    """Без пригодных точек в окне 0..11 NowCast не определён."""
    assert aqi.nowcast(values) is None


@pytest.mark.parametrize("values,expected", [
    # вес = 1 - (20-10)/20 = 0.5; (20*1 + 10*0.5) / (1 + 0.5) = 25/1.5
    pytest.param({0: 20, 1: 10}, 25 / 1.5, id="nowcast-reference-16.67"),
    # одна точка: вес = 1, среднее равно ей самой (в любом часе окна)
    pytest.param({0: 42}, 42.0, id="nowcast-single-point-hour-0"),
    pytest.param({5: 42}, 42.0, id="nowcast-single-point-hour-5"),
    pytest.param({11: 42}, 42.0, id="nowcast-single-point-hour-11"),
    # все значения равны -> вес 1 -> обычное среднее
    pytest.param({0: 10, 1: 10, 2: 10}, 10.0, id="nowcast-equal-values-weight-1"),
    # None внутри словаря выпадает, остаются часы 0 и 2:
    # вес = 0.5; (20*1 + 10*0.25) / (1 + 0.25) = 22.5/1.25 = 18.0
    pytest.param({0: 20, 1: None, 2: 10}, 18.0, id="nowcast-none-ignored"),
    # час 12 вне окна -> остаётся только час 0
    pytest.param({12: 100, 0: 10}, 10.0, id="nowcast-hour-12-dropped"),
    pytest.param({-1: 100, 0: 10}, 10.0, id="nowcast-hour-neg-1-dropped"),
])
def test_nowcast_values(values, expected):
    """Взвешенное среднее NowCast; ожидания посчитаны по формуле EPA вручную."""
    assert aqi.nowcast(values) == pytest.approx(expected, abs=1e-9)


def test_nowcast_all_zeros_short_circuits_to_zero():
    """cmax <= 0: вес не считается (иначе деление на ноль), сразу 0.0."""
    assert aqi.nowcast({0: 0, 1: 0}) == 0.0
    assert aqi.nowcast({0: 0.0}) == 0.0


def test_nowcast_weight_is_clamped_to_half():
    """Вес зажат снизу в 0.5: при сильном разбросе старые часы не обнуляются.

    Для {0: 100, 1: 1} сырой вес = 1 - 99/100 = 0.01. Без зажима результат
    был бы (100 + 1*0.01) / (1 + 0.01) ~= 99.02, то есть NowCast схлопнулся бы
    до последнего часа. С зажимом: (100 + 1*0.5) / (1 + 0.5) = 67.0.
    """
    raw_weight = 0.01
    unclamped = (100 + 1 * raw_weight) / (1 + raw_weight)

    assert aqi.nowcast({0: 100, 1: 1}) == pytest.approx(100.5 / 1.5, abs=1e-9)
    assert aqi.nowcast({0: 100, 1: 1}) != pytest.approx(unclamped, abs=1e-2)


def test_nowcast_equal_values_is_plain_average():
    """При нулевом разбросе вес равен 1 и NowCast вырождается в простое среднее.

    Изначально тест назывался «проверка верхнего зажима min(1.0, ...)», но эта
    ветка недостижима: сырой вес 1-(cmax-cmin)/cmax при cmax > 0 не бывает больше
    единицы, а при cmax <= 0 функция выходит раньше. Проверяется, стало быть,
    именно вырождение в среднее — зажим сверху в aqi.nowcast мёртвый.
    """
    values = {h: 30.0 for h in range(12)}
    assert aqi.nowcast(values) == pytest.approx(30.0, abs=1e-9)


def test_nowcast_recent_hours_weigh_more():
    """Инвариант метода: свежий час тянет результат сильнее старого."""
    rising = aqi.nowcast({0: 100, 1: 10})   # свежий час высокий
    falling = aqi.nowcast({0: 10, 1: 100})  # высокий час — старый
    assert rising > falling


# --------------------------------------------------------------------------
# Инварианты
# --------------------------------------------------------------------------
def test_sub_index_pm25_is_monotonic():
    """Инвариант: рост концентрации не может уменьшить AQI."""
    grid = [round(i * 0.05, 10) for i in range(0, 8001)]  # 0.0 .. 400.0
    previous_conc, previous_value = grid[0], aqi.sub_index_pm25(grid[0])
    for conc in grid[1:]:
        value = aqi.sub_index_pm25(conc)
        assert value >= previous_value, (
            f"sub_index_pm25 убывает: {previous_conc} -> {previous_value}, "
            f"но {conc} -> {value}"
        )
        previous_conc, previous_value = conc, value


def test_sub_index_pm10_is_monotonic():
    """Инвариант: то же для PM10 на целой сетке."""
    previous_conc, previous_value = 0, aqi.sub_index_pm10(0)
    for conc in range(1, 701):
        value = aqi.sub_index_pm10(conc)
        assert value >= previous_value, (
            f"sub_index_pm10 убывает: {previous_conc} -> {previous_value}, "
            f"но {conc} -> {value}"
        )
        previous_conc, previous_value = conc, value


@pytest.mark.parametrize("conc", [0.0, 0.3, 9.0, 9.1, 35.4, 55.4, 125.5, 325.4, 400.0])
def test_sub_index_pm25_wrapper_matches_explicit_table(conc):
    """sub_index_pm25 — это ровно sub_index(PM25_BP, шаг 0.1), без своей логики."""
    assert aqi.sub_index_pm25(conc) == aqi.sub_index(conc, aqi.PM25_BP, 0.1)


@pytest.mark.parametrize("conc", [0, 54, 55, 155, 254, 425, 604, 700])
def test_sub_index_pm10_wrapper_matches_explicit_table(conc):
    """sub_index_pm10 — это ровно sub_index(PM10_BP, шаг 1)."""
    assert aqi.sub_index_pm10(conc) == aqi.sub_index(conc, aqi.PM10_BP, 1)


def test_breakpoint_tables_are_contiguous_and_ordered():
    """Инвариант таблиц: строки идут по возрастанию и без дыр на своей сетке."""
    for table, step in ((aqi.PM25_BP, 0.1), (aqi.PM10_BP, 1)):
        for (_, chi, _, ihi), (clo_next, _, ilo_next, _) in zip(table, table[1:]):
            assert round(clo_next - chi, 10) == step, "разрыв в шкале концентраций"
            assert ilo_next == ihi + 1, "разрыв в шкале AQI"
