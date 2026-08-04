"""Тесты HTTP-слоя приложения через fastapi.testclient.TestClient.

Фикстуры (client, auth_client, db_path, insert_measurement) и константы
PUSH_USER/PUSH_PASS живут в tests/conftest.py — здесь только тесты.

Время в тестах истории заморожено (фикстура frozen_now): иначе граница
календарных суток делала бы результат зависимым от момента запуска.
"""

import base64
import time
from datetime import datetime, timezone

import pytest

from conftest import PUSH_PASS, PUSH_USER
from server import aqi, auth, history
from server.routes import push as push_routes

# Опорный момент: среда 2026-03-11 15:20:00 UTC. Среда — чтобы «неделя с
# понедельника» отличалась и от начала суток, и от начала месяца.
NOW = int(datetime(2026, 3, 11, 15, 20, 0, tzinfo=timezone.utc).timestamp())


def _ts(year, month, day, hour=0, minute=0):
    """Epoch-секунды для момента в UTC — чтобы не писать магические числа."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def frozen_now(monkeypatch):
    """Замораживает time.time(), который роуты зовут в push/current/history."""
    monkeypatch.setattr(time, "time", lambda: float(NOW))
    return NOW


def _push_body(**values):
    """Тело push в формате прошивки airRohr из пар value_type -> value."""
    return {
        "esp8266id": "2998975",
        "sensordatavalues": [
            {"value_type": k, "value": str(v)} for k, v in values.items()
        ],
    }


# --------------------------------------------------------------------------
# period_window — чистая функция, тестируем без HTTP
# --------------------------------------------------------------------------

def test_period_window_end_is_floor_of_now_to_bucket():
    """end — начало корзины, в которую попадает now (обычно ещё не полной),
    а не now дословно. 24h: bucket=1ч, 15:20 -> корзина с 15:00."""
    start, end = history.period_window(NOW, "24h", 0, None)
    assert end == _ts(2026, 3, 11, 15, 0)
    assert start == end - 24 * 3600


def test_period_window_span_is_end_minus_span():
    """Для окна фиксированной длины start = end - span, счёт не от эпохи."""
    start, end = history.period_window(NOW, "7d", 0, None)
    assert end == _ts(2026, 3, 11, 15, 0)
    assert start == end - 7 * 86400


def test_period_window_offset_shifts_hourly_grid():
    """tz_offset двигает сетку и для часовых корзин, не только суточных:
    +90 и -90 мин одинаково уводят границу с :00 на :30 (симметрично
    относительно 15:20, поэтому оба смещения дают один и тот же результат)."""
    start_pos, end_pos = history.period_window(NOW, "24h", 90, None)
    start_neg, end_neg = history.period_window(NOW, "24h", -90, None)
    assert end_pos == end_neg == _ts(2026, 3, 11, 14, 30)
    assert start_pos == end_pos - 24 * 3600


@pytest.mark.parametrize("tz_offset,expected_start,expected_end", [
    (0, (2026, 2, 9, 0, 0), (2026, 3, 11, 0, 0)),
    (180, (2026, 2, 8, 21, 0), (2026, 3, 10, 21, 0)),
    (-300, (2026, 2, 9, 5, 0), (2026, 3, 11, 5, 0)),
])
def test_period_window_offset_shifts_daily_grid(tz_offset, expected_start, expected_end):
    """30d: bucket суточный, tz_offset ставит сетку на местную полночь,
    а не на полночь UTC."""
    start, end = history.period_window(NOW, "30d", tz_offset, None)
    assert start == _ts(*expected_start)
    assert end == _ts(*expected_end)


def test_period_window_all_starts_at_first_measurement_bucket():
    """all: начало — корзина, в которую попадает первое измерение, а не эпоха."""
    first_ts = _ts(2025, 6, 15, 10, 0)
    start, end = history.period_window(NOW, "all", 0, first_ts)
    bucket = history.PERIODS["all"][1]
    assert start <= first_ts < start + bucket
    assert start % bucket == 0


def test_period_window_all_with_no_data_start_equals_end():
    """all и БД пуста (first_ts=None) — окно вырождается в одну точку."""
    start, end = history.period_window(NOW, "all", 0, None)
    assert start == end


@pytest.mark.parametrize("period", sorted(history.PERIODS))
@pytest.mark.parametrize("tz_offset", [-840, -300, 0, 180, 840])
def test_period_window_never_in_future(period, tz_offset):
    """Конец окна не может оказаться позже текущего момента, начало — не позже конца."""
    start, end = history.period_window(NOW, period, tz_offset, None)
    assert end <= NOW
    assert start <= end


# --------------------------------------------------------------------------
# push — разбор тела и маппинг полей
# --------------------------------------------------------------------------

def test_push_maps_all_bme280_fields(client):
    """Полный набор value_type прошивки раскладывается по своим колонкам."""
    r = client.post("/api/v1/push", json=_push_body(
        SDS_P1="12.3", SDS_P2="7.1",
        BME280_temperature="21.5", BME280_humidity="48.25",
        BME280_pressure="98500.0", signal="-67",
    ))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    cur = client.get("/api/v1/current").json()
    assert cur["pm10"] == 12.3
    assert cur["pm25"] == 7.1
    assert cur["temperature"] == 21.5
    assert cur["humidity"] == 48.25
    assert cur["pressure"] == 985.0
    assert cur["signal"] == -67.0


def test_push_maps_bmp280_fields(client):
    """У BMP280 (без датчика влажности) свои value_type — тоже в маппинге."""
    r = client.post("/api/v1/push", json=_push_body(
        BMP280_temperature="19.75", BMP280_pressure="99000.0"))
    assert r.status_code == 200, r.text

    cur = client.get("/api/v1/current").json()
    assert cur["temperature"] == 19.75
    assert cur["pressure"] == 990.0
    assert cur["humidity"] is None


def test_push_pressure_above_threshold_is_pascals(client):
    """Больше 2000 — это паскали, делим на 100 и округляем до сотых."""
    r = client.post("/api/v1/push", json=_push_body(BME280_pressure="2001.0"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["pressure"] == 20.01


def test_push_pressure_at_threshold_is_kept(client):
    """Ровно 2000 — граница не включена в «паскали», значение не трогаем."""
    r = client.post("/api/v1/push", json=_push_body(BME280_pressure="2000.0"))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["pressure"] == 2000.0


def test_push_first_duplicate_value_type_wins(client):
    """При дублях value_type побеждает первый (в коде `col in row` -> continue)."""
    r = client.post("/api/v1/push", json={"sensordatavalues": [
        {"value_type": "SDS_P1", "value": "12.3"},
        {"value_type": "SDS_P1", "value": "99.9"},
    ]})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["pm10"] == 12.3


@pytest.mark.parametrize("bad_value", ["abc", "", None, "inf", "-inf", "nan"])
def test_push_skips_non_numeric_and_non_finite(client, bad_value):
    """Нечисловое и inf/nan молча пропускаются, соседние поля не страдают."""
    r = client.post("/api/v1/push", json={"sensordatavalues": [
        {"value_type": "SDS_P1", "value": bad_value},
        {"value_type": "SDS_P2", "value": "7.1"},
    ]})
    assert r.status_code == 200, r.text

    cur = client.get("/api/v1/current").json()
    assert cur["pm10"] is None
    assert cur["pm25"] == 7.1


def test_push_skips_garbage_items(client):
    """Элементы-не-словари и неизвестные value_type игнорируются."""
    r = client.post("/api/v1/push", json={"sensordatavalues": [
        "мусор",
        42,
        None,
        {"value_type": "GPS_lat", "value": "55.75"},
        {"no_value_type": "SDS_P1", "value": "1.0"},
        {"value_type": "SDS_P2", "value": "7.1"},
    ]})
    assert r.status_code == 200, r.text

    cur = client.get("/api/v1/current").json()
    assert cur["pm25"] == 7.1
    assert cur["pm10"] is None


def test_push_sensor_id_from_esp8266id(client):
    r = client.post("/api/v1/push", json={
        "esp8266id": 2998975,
        "esp32id": "ignored",
        "sensordatavalues": [],
    })
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["sensor_id"] == "2998975"


def test_push_sensor_id_falls_back_to_esp32id(client):
    r = client.post("/api/v1/push", json={"esp32id": "abc123", "sensordatavalues": []})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["sensor_id"] == "abc123"


def test_push_sensor_id_falls_back_to_header(client):
    r = client.post("/api/v1/push", json={"sensordatavalues": []},
                    headers={"X-Sensor": "from-header"})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["sensor_id"] == "from-header"


def test_push_sensor_id_empty_when_unknown(client):
    r = client.post("/api/v1/push", json={"sensordatavalues": []})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/current").json()["sensor_id"] == ""


def test_push_returns_server_timestamp(client, frozen_now):
    """ts в ответе — серверное время приёма, а не что-то из тела."""
    r = client.post("/api/v1/push", json={"sensordatavalues": [], "ts": 1})
    assert r.json() == {"ok": True, "ts": frozen_now}


# --------------------------------------------------------------------------
# push — ошибки запроса
# --------------------------------------------------------------------------

def test_push_invalid_json(client):
    r = client.post("/api/v1/push", content=b"{not json")
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid JSON"


def test_push_missing_sensordatavalues(client):
    r = client.post("/api/v1/push", json={"esp8266id": "2998975"})
    assert r.status_code == 400
    assert r.json()["detail"] == "sensordatavalues missing"


@pytest.mark.parametrize("payload", [
    {"sensordatavalues": "не список"},
    {"sensordatavalues": {"value_type": "SDS_P1"}},
    {"sensordatavalues": None},
    [{"value_type": "SDS_P1", "value": "1.0"}],  # верхний уровень — список
])
def test_push_rejects_wrong_sensordatavalues(client, payload):
    r = client.post("/api/v1/push", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"] == "sensordatavalues missing"


def test_push_body_too_large(client):
    """Тело больше MAX_PUSH_BODY отбивается на лету, до разбора JSON."""
    r = client.post("/api/v1/push", content=b"x" * (push_routes.MAX_PUSH_BODY + 1))
    assert r.status_code == 413
    assert client.get("/healthz").json()["rows"] == 0


def test_push_body_at_limit_is_parsed(client):
    """Ровно MAX_PUSH_BODY байт — не «too large»: до разбора доходит, падает
    уже на JSON (нужен именно 400, а не 413)."""
    r = client.post("/api/v1/push", content=b"x" * push_routes.MAX_PUSH_BODY)
    assert r.status_code == 400


# --------------------------------------------------------------------------
# push — basic auth
# --------------------------------------------------------------------------

def test_push_without_auth_header_is_401(auth_client):
    r = auth_client.post("/api/v1/push", json={"sensordatavalues": []})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"


def test_push_wrong_password_is_401(auth_client):
    r = auth_client.post("/api/v1/push", json={"sensordatavalues": []},
                         auth=(PUSH_USER, PUSH_PASS + "x"))
    assert r.status_code == 401


def test_push_wrong_user_is_401(auth_client):
    r = auth_client.post("/api/v1/push", json={"sensordatavalues": []},
                         auth=("не-тот", PUSH_PASS))
    assert r.status_code == 401


def test_push_valid_credentials_pass(auth_client):
    r = auth_client.post("/api/v1/push", json=_push_body(SDS_P2="7.1"),
                         auth=(PUSH_USER, PUSH_PASS))
    assert r.status_code == 200, r.text
    assert auth_client.get("/api/v1/current").json()["pm25"] == 7.1


def _basic(user, password):
    """Заголовок Basic из пары логин/пароль.

    Собираем, а не пишем base64-литералом: захардкоженный
    'basic dHU0a2E6...' неотличим от утёкших кред для секрет-сканеров и
    исправно поднимает ложную тревогу (уже поднимал). Плюс креды теперь
    в одном месте — в conftest.
    """
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize("header", [
    "Basic !!!not-base64!!!",         # не декодируется (binascii.Error)
    "Basic " + base64.b64encode(b"no-colon-here").decode(),  # нет разделителя
    "Bearer token",                   # не та схема
    # Схема с маленькой буквы: креды верные, но auth.py проверяет
    # header.startswith("Basic ") — регистр важен, ждём 401.
    _basic(PUSH_USER, PUSH_PASS).replace("Basic ", "basic ", 1),
    "",                               # пустой заголовок
])
def test_push_broken_authorization_is_401_not_500(auth_client, header):
    """Любой мусор в Authorization — это 401, а не необработанное исключение."""
    r = auth_client.post("/api/v1/push", json={"sensordatavalues": []},
                         headers={"Authorization": header})
    assert r.status_code == 401


def test_push_non_ascii_credentials_is_401(auth_client):
    """secrets.compare_digest падает на не-ASCII строках — это должно быть
    поймано и отдано как 401."""
    header = "Basic " + base64.b64encode("юзер:пароль".encode()).decode()
    r = auth_client.post("/api/v1/push", json={"sensordatavalues": []},
                         headers={"Authorization": header})
    assert r.status_code == 401


def test_push_without_password_configured_needs_no_auth(client):
    """Документированное поведение: пустой TU4KA_PUSH_PASS отключает проверку
    (в лог при старте пишется предупреждение). Фикстура client как раз такая."""
    assert auth.PUSH_PASS == ""
    r = client.post("/api/v1/push", json=_push_body(SDS_P2="7.1"))
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------
# /api/v1/current
# --------------------------------------------------------------------------

def test_current_empty_db(client):
    r = client.get("/api/v1/current")
    assert r.status_code == 200
    assert r.json() == {"ts": None}


def test_current_returns_age_and_nowcast_aqi(client, insert_measurement, frozen_now):
    insert_measurement(ts=frozen_now - 120, pm25=20.0, pm10=30.0)

    d = client.get("/api/v1/current").json()
    assert d["ts"] == frozen_now - 120
    assert d["age_s"] == 120
    assert d["aqi"]["method"] == "nowcast_12h"
    assert d["aqi"]["dominant"] == "pm25"
    assert d["aqi"]["category"] == "moderate"
    # Одна точка -> NowCast равен ей самой. По таблице EPA:
    # pm25 20.0 -> 49/26.3*(20.0-9.1)+51 = 71.31 -> 71
    # pm10 30.0 -> 50/54*30 = 27.8 -> 28
    assert d["aqi"]["aqi"] == 71
    assert d["aqi"]["pm25_aqi"] == 71
    assert d["aqi"]["pm10_aqi"] == 28


def test_current_nowcast_weighs_hours(client, insert_measurement, frozen_now):
    """NowCast взвешивает часы, а не усредняет: свежий час весит больше старого.

    Точки в часах ago=0 и ago=2 (frozen_now = 15:20 UTC, так что -60 с остаётся
    в том же часовом бакете, а -2 ч уходит ровно на два).
    cmax=100, cmin=10 -> вес 1-(100-10)/100 = 0.1, зажим до 0.5.
    NowCast = (100*0.5^0 + 10*0.5^2) / (0.5^0 + 0.5^2) = 102.5/1.25 = 82.0
    sub_index_pm25(82.0) = 49/69.9*(82.0-55.5)+151 = 169.58 -> 170.
    Простое среднее дало бы 55.0 (AQI 149) — числа заведомо различимы.
    """
    insert_measurement(ts=frozen_now - 60, pm25=100.0)
    insert_measurement(ts=frozen_now - 2 * 3600, pm25=10.0)

    d = client.get("/api/v1/current").json()
    assert d["aqi"]["aqi"] == 170
    assert d["aqi"]["dominant"] == "pm25"


def test_current_nowcast_window_is_12_hours(client, insert_measurement, frozen_now):
    """Точка старше 12 часов в NowCast не участвует."""
    insert_measurement(ts=frozen_now - 13 * 3600, pm25=300.0)
    insert_measurement(ts=frozen_now - 60, pm25=10.0)

    d = client.get("/api/v1/current").json()
    # осталась только свежая точка: 49/26.3*(10.0-9.1)+51 = 52.68 -> 53
    assert d["aqi"]["aqi"] == 53


def test_current_without_pm_has_empty_aqi(client, insert_measurement, frozen_now):
    """BMP280 без SDS011: измерение есть, PM нет — AQI полностью null,
    но ключ method на месте, чтобы клиент не спотыкался о его отсутствие."""
    insert_measurement(ts=frozen_now - 60, temperature=5.0, pressure=1013.0)

    d = client.get("/api/v1/current").json()
    assert d["aqi"] == {"aqi": None, "category": None, "dominant": None,
                        "pm25_aqi": None, "pm10_aqi": None, "method": "nowcast_12h"}


def test_current_age_is_not_negative(client, insert_measurement, frozen_now):
    """Измерение «из будущего» (расхождение часов) не даёт отрицательный age_s."""
    insert_measurement(ts=frozen_now + 600, pm25=5.0)
    assert client.get("/api/v1/current").json()["age_s"] == 0


def test_current_takes_latest_measurement(client, insert_measurement, frozen_now):
    insert_measurement(ts=frozen_now - 3600, pm25=10.0, pm10=20.0)
    insert_measurement(ts=frozen_now - 60, pm25=30.0, pm10=40.0)

    d = client.get("/api/v1/current").json()
    assert d["ts"] == frozen_now - 60
    assert d["pm25"] == 30.0
    assert d["pm10"] == 40.0


def test_current_round_trip_after_push(client):
    """push -> current отдаёт ровно те же значения (давление — уже в гПа)."""
    r = client.post("/api/v1/push", json=_push_body(
        SDS_P1="18.4", SDS_P2="9.6",
        BME280_temperature="-3.25", BME280_humidity="91.5",
        BME280_pressure="101325.0",
    ))
    assert r.status_code == 200, r.text
    pushed_ts = r.json()["ts"]

    d = client.get("/api/v1/current").json()
    assert d["ts"] == pushed_ts
    assert d["pm10"] == 18.4
    assert d["pm25"] == 9.6
    assert d["temperature"] == -3.25
    assert d["humidity"] == 91.5
    assert d["pressure"] == 1013.25


# --------------------------------------------------------------------------
# /api/v1/history
# --------------------------------------------------------------------------

def test_history_rejects_unknown_period(client):
    r = client.get("/api/v1/history", params={"period": "year"})
    assert r.status_code == 400
    assert "period must be one of" in r.json()["detail"]


@pytest.mark.parametrize("period,bucket", [
    ("24h", 3600), ("7d", 3 * 3600), ("30d", 86400),
    ("12m", 2 * 86400), ("all", 15 * 86400),
])
def test_history_bucket_per_period(client, period, bucket):
    d = client.get("/api/v1/history", params={"period": period}).json()
    assert d["period"] == period
    assert d["bucket_s"] == bucket


@pytest.mark.parametrize("tz_offset,status", [
    (840, 200), (841, 400), (-840, 200), (-841, 400), (0, 200),
])
def test_history_tz_offset_bounds(client, tz_offset, status):
    """MAX_TZ_OFFSET = ±840 минут, границы включены."""
    r = client.get("/api/v1/history", params={"period": "24h", "tz_offset": tz_offset})
    assert r.status_code == status


@pytest.mark.parametrize("tz_offset,expected_start,expected_end", [
    (0, (2026, 2, 9, 0, 0), (2026, 3, 11, 0, 0)),
    (180, (2026, 2, 8, 21, 0), (2026, 3, 10, 21, 0)),
    (-300, (2026, 2, 9, 5, 0), (2026, 3, 11, 5, 0)),
])
def test_history_start_follows_tz_offset(client, frozen_now, tz_offset,
                                         expected_start, expected_end):
    """tz_offset должен доезжать до сетки корзин, а не только валидироваться.

    30d выбран специально: у него суточная корзина, поэтому сдвиг пояса
    сразу виден на start/end (в отличие от 24h/7d, где сетка часовая и
    смещение на целое число часов её не меняет). Ожидания — литералы, а не
    вызов history.period_window: иначе тест повторил бы реализацию и не заметил,
    если бы history перестала передавать пояс дальше.
    """
    d = client.get("/api/v1/history",
                   params={"period": "30d", "tz_offset": tz_offset}).json()
    assert d["start"] == _ts(*expected_start)


def test_history_buckets_are_anchored_to_tz_shifted_grid_not_epoch(
        client, insert_measurement, frozen_now):
    """Корзины отсчитываются от сетки period_window (сдвинутой на tz_offset),
    а не от эпохи.

    Кейс подобран так, чтобы две привязки расходились: period=30d с
    tz_offset=330 (Индия, UTC+5:30) даёт start, который не кратен 86400
    (сетке от эпохи). При эпохальной привязке измерение получило бы t,
    которого нет в сетке range(start, end+1, bucket), и молча пропало бы
    из выдачи.
    """
    start, end = history.period_window(frozen_now, "30d", 330, None)
    assert start == _ts(2026, 2, 8, 18, 30)
    assert start % 86400 != 0, "кейс бесполезен: сетки совпали, расхождение не поймать"

    insert_measurement(ts=start + 100, pm25=10.0, pm10=20.0)
    d = client.get("/api/v1/history",
                   params={"period": "30d", "tz_offset": 330}).json()

    assert d["start"] == start
    assert d["points"][0]["t"] == start
    first = next(p for p in d["points"] if p["t"] == start)
    assert first["n"] == 1
    assert first["pm25"] == 10.0


def test_history_grid_is_dense_and_starts_at_window_start(client, frozen_now):
    """Сетка плотная: от start ровным шагом bucket_s, пустые корзины
    приходят с n=0, null-метриками и null aqi/aqi_lo/aqi_hi."""
    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()

    start, end = history.period_window(frozen_now, "24h", 0, None)
    assert d["start"] == start == _ts(2026, 3, 10, 15, 0)
    assert d["end"] == frozen_now

    points = d["points"]
    assert points[0]["t"] == start
    assert [p["t"] for p in points] == list(range(start, end + 1, 3600))
    assert len(points) == 25
    for p in points:
        assert p["n"] == 0
        assert p["pm25"] is None and p["pm10"] is None
        assert p["temperature"] is None and p["humidity"] is None
        assert p["pressure"] is None
        assert p["aqi"] is None and p["aqi_lo"] is None and p["aqi_hi"] is None
        for metric in ("pm25", "pm10", "temperature", "humidity", "pressure"):
            assert p[metric + "_lo"] is None and p[metric + "_hi"] is None


def test_history_metric_lo_hi_bracket_the_bucket_average(client, insert_measurement,
                                                          frozen_now):
    """<metric>_lo/_hi — минимум/максимум самой метрики внутри корзины
    (лента разброса на графике при выборе метрики), не только у PM/AQI."""
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    base = start + 2 * 3600
    insert_measurement(ts=base + 10, temperature=5.0, humidity=40.0, pressure=1000.0)
    insert_measurement(ts=base + 200, temperature=9.0, humidity=60.0, pressure=1004.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == base)
    assert point["temperature_lo"] == 5.0 and point["temperature_hi"] == 9.0
    assert point["humidity_lo"] == 40.0 and point["humidity_hi"] == 60.0
    assert point["pressure_lo"] == 1000.0 and point["pressure_hi"] == 1004.0
    for metric in ("temperature", "humidity", "pressure"):
        assert point[metric + "_lo"] <= point[metric] <= point[metric + "_hi"]


def test_history_measurement_lands_in_its_bucket(client, insert_measurement, frozen_now):
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    ts = start + 3600 + 100  # середина второй корзины (часовой)
    insert_measurement(ts=ts, pm25=10.0, pm10=20.0, temperature=5.5)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    expected_t = start + ((ts - start) // 3600) * 3600
    assert expected_t == start + 3600

    filled = [p for p in d["points"] if p["n"] > 0]
    assert len(filled) == 1
    assert filled[0]["t"] == expected_t
    assert filled[0]["pm25"] == 10.0
    assert filled[0]["pm10"] == 20.0
    assert filled[0]["temperature"] == 5.5
    # мгновенный AQI корзины: pm25=10 доминирует над pm10=20,
    # 49/26.3*(10.0-9.1)+51 = 52.68 -> 53. Единственное измерение — lo/hi совпадают с aqi.
    assert filled[0]["aqi"] == 53
    assert filled[0]["aqi_lo"] == 53
    assert filled[0]["aqi_hi"] == 53


def test_history_averages_within_bucket(client, insert_measurement, frozen_now):
    """Несколько измерений в одной корзине усредняются, n — их количество."""
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    base = start + 2 * 3600
    insert_measurement(ts=base + 10, pm25=10.0, pm10=20.0)
    insert_measurement(ts=base + 200, pm25=30.0, pm10=41.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == base)
    assert point["n"] == 2
    assert point["pm25"] == 20.0
    assert point["pm10"] == 30.5


def test_history_aqi_lo_hi_bracket_the_bucket_average(client, insert_measurement,
                                                       frozen_now):
    """aqi_lo/aqi_hi — по минимуму/максимуму PM в корзине, а не по среднему:
    lo <= aqi <= hi, и hi считается по худшему (максимальному) измерению."""
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    base = start + 2 * 3600
    insert_measurement(ts=base + 10, pm25=5.0, pm10=10.0)
    insert_measurement(ts=base + 200, pm25=30.0, pm10=60.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == base)
    assert point["aqi_lo"] <= point["aqi"] <= point["aqi_hi"]
    assert point["aqi_lo"] < point["aqi_hi"]
    # lo = compute(min_pm25=5.0, min_pm10=10.0) = 28, hi = compute(max_pm25=30.0, max_pm10=60.0) = 90,
    # avg-корзина compute(17.5, 35.0) = 67 — строго между ними.
    assert point["aqi_lo"] == 28
    assert point["aqi_hi"] == 90
    assert point["aqi"] == 67


def test_history_ignores_measurements_before_window_start(client, insert_measurement,
                                                           frozen_now):
    """Точка перед началом окна не попадает в выдачу."""
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    insert_measurement(ts=start - 60, pm25=99.0, pm10=99.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    assert all(p["n"] == 0 for p in d["points"])


def test_history_includes_measurement_exactly_at_window_start(
        client, insert_measurement, frozen_now):
    """Граница включающая: WHERE ts >= start, а не ts > start.

    Точка ровно в момент start обязана попасть в нулевую корзину — иначе
    самое старое измерение окна теряется, если оно пришло ровно на границе.
    """
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    insert_measurement(ts=start, pm25=10.0, pm10=20.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    assert d["points"][0]["t"] == start
    assert d["points"][0]["n"] == 1
    assert d["points"][0]["pm25"] == 10.0


def test_history_7d_window_covers_full_seven_days(client, insert_measurement, frozen_now):
    """Недельное окно — последние 7×24 часа от now, а не с понедельника."""
    start, end = history.period_window(frozen_now, "7d", 0, None)
    assert end - start == 7 * 86400
    insert_measurement(ts=start + 1800, pm25=12.0, pm10=15.0)

    d = client.get("/api/v1/history", params={"period": "7d", "tz_offset": 0}).json()
    assert d["start"] == start
    point = next(p for p in d["points"] if p["t"] == start)
    assert point["n"] == 1
    assert point["pm25"] == 12.0


def test_history_all_starts_at_first_measurement(client, insert_measurement, frozen_now):
    """period=all — окно от корзины первого измерения, а не от эпохи."""
    first_ts = frozen_now - 200 * 86400
    insert_measurement(ts=first_ts, pm25=8.0, pm10=16.0)

    d = client.get("/api/v1/history", params={"period": "all", "tz_offset": 0}).json()
    start, _ = history.period_window(frozen_now, "all", 0, first_ts)
    assert d["start"] == start
    assert d["points"][0]["t"] == start
    assert d["points"][0]["n"] == 1


def test_history_counts_rows_not_values(client, insert_measurement, frozen_now):
    """n — число строк в корзине, а не число непустых значений метрики:
    корзина с одной лишь температурой всё равно приходит с n=1 и pm25=None.
    Поведение as-is, см. отчёт."""
    start, _ = history.period_window(frozen_now, "24h", 0, None)
    insert_measurement(ts=start + 600, temperature=7.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == start)
    assert point["n"] == 1
    assert point["temperature"] == 7.0
    assert point["pm25"] is None
    assert point["aqi"] is None
    assert point["aqi_lo"] is None and point["aqi_hi"] is None


def test_history_drops_measurements_newer_than_end(client, insert_measurement,
                                                   frozen_now):
    """Измерение с ts из будущего в выдачу не попадает: сетка обрывается на
    текущей корзине, а SQL-корзина уезжает за неё. Поведение as-is, см. отчёт."""
    insert_measurement(ts=frozen_now + 3600, pm25=50.0, pm10=60.0)

    d = client.get("/api/v1/history", params={"period": "24h", "tz_offset": 0}).json()
    assert d["points"][-1]["t"] <= d["end"]
    assert all(p["n"] == 0 for p in d["points"])
    # строка при этом в БД есть — потерялась именно выдача
    assert client.get("/healthz").json()["rows"] == 1


# --------------------------------------------------------------------------
# /api/v1/hours — профиль суток
# --------------------------------------------------------------------------

def _hours_window(tz_offset=0):
    """Границы окна профиля на замороженном NOW — как их считает history.

    Окно кончается на границе текущего часа, поэтому start приходится не на
    полночь (на NOW — на 15:00 UTC): индекс столбика считать через _hour_of.
    """
    shift = tz_offset * 60
    end = ((NOW + shift) // 3600) * 3600 - shift
    return end - history.HOURS_PROFILE_DAYS * 86400, end


def _hour_of(ts, tz_offset=0):
    """Номер столбика профиля, в который попадает измерение."""
    return ((ts + tz_offset * 60) // 3600) % 24


def test_hours_profile_is_dense_on_empty_db(client, frozen_now):
    """24 часа по возрастанию, час без измерений — null и n=0, дыр в JSON нет."""
    d = client.get("/api/v1/hours").json()
    start, end = _hours_window()
    assert (d["window_days"], d["tz_offset"]) == (7, 0)
    assert (d["start"], d["end"]) == (start, end)
    assert d["days_covered"] == 0
    assert [h["hour"] for h in d["hours"]] == list(range(24))
    assert all(h["aqi"] is None and h["pm25"] is None and h["pm10"] is None
               and h["n"] == 0 and h["days"] == 0 for h in d["hours"])


def test_hours_profile_survives_a_full_day_gap(client, insert_measurement, frozen_now):
    """Ради чего всё затевалось: сутки простоя не выедают в профиле дыру.

    Датчик писал шесть суток и замолчал на последние 24 часа (свет выключили).
    Прежний расчёт по одному окну 24h оставил бы 24 пустых столбика; профиль
    усредняет по суткам окна, так что заполнены все часы.
    """
    _, end = _hours_window()
    for i in range(25, 7 * 24 + 1):  # от 25 часов назад до края окна
        insert_measurement(ts=end - i * 3600, pm25=10.0, pm10=20.0)

    d = client.get("/api/v1/hours").json()
    assert all(h["aqi"] is not None and h["n"] > 0 for h in d["hours"])
    assert d["days_covered"] == 7  # шесть полных суток + хвост седьмых

    # при этом окно 24h честно пустое — сводка за сутки это и покажет
    day = client.get("/api/v1/history", params={"period": "24h"}).json()
    assert all(p["aqi"] is None for p in day["points"])


def test_hours_profile_fills_only_hours_with_data(client, insert_measurement,
                                                  frozen_now):
    start, _ = _hours_window()
    ts = start + 9 * 3600 + 600
    h = _hour_of(ts)
    insert_measurement(ts=ts, pm25=12.0, pm10=24.0)

    hours = client.get("/api/v1/hours").json()["hours"]
    assert hours[h]["n"] == 1 and hours[h]["pm25"] == 12.0
    assert hours[h]["aqi"] == aqi.compute(12.0, 24.0)["aqi"]
    assert all(x["n"] == 0 and x["aqi"] is None for x in hours if x["hour"] != h)


def test_hours_profile_groups_by_local_hour_not_utc(client, insert_measurement,
                                                    frozen_now):
    """Час считается по местному времени клиента (tz_offset), а не по UTC.

    На tz_offset=0 регрессия не видна — берём UTC+5:30, где часы расходятся:
    20:00 UTC — это 01:30 по месту, то есть столбик 01, а не 20.
    """
    insert_measurement(ts=_ts(2026, 3, 10, 20, 0), pm25=15.0, pm10=30.0)

    hours = client.get("/api/v1/hours", params={"tz_offset": 330}).json()["hours"]
    assert hours[1]["n"] == 1
    assert hours[20]["n"] == 0


def test_hours_profile_window_is_seven_days(client, insert_measurement, frozen_now):
    """Измерение старше окна в профиль не попадает, свежее — попадает."""
    start, _ = _hours_window()
    insert_measurement(ts=start - 3600, pm25=99.0, pm10=99.0)  # за краем окна
    insert_measurement(ts=start + 3600, pm25=10.0, pm10=20.0)

    hours = client.get("/api/v1/hours").json()["hours"]
    assert sum(h["n"] for h in hours) == 1
    assert hours[_hour_of(start + 3600)]["pm25"] == 10.0


def test_hours_profile_aqi_is_computed_from_average_pm(client, insert_measurement,
                                                       frozen_now):
    """AQI часа — по средним PM часа, как у корзины /history (A4), а не среднее
    от AQI отдельных измерений: на разбросе 1 → 100 это разные числа."""
    start, _ = _hours_window()
    insert_measurement(ts=start + 3600, pm25=1.0)
    insert_measurement(ts=start + 86400 + 3600, pm25=100.0)  # тот же час, другие сутки

    hour = client.get("/api/v1/hours").json()["hours"][_hour_of(start + 3600)]
    assert hour["pm25"] == 50.5
    assert hour["aqi"] == aqi.compute(50.5, None)["aqi"]
    mean_of_aqi = (aqi.compute(1.0, None)["aqi"] + aqi.compute(100.0, None)["aqi"]) / 2
    assert hour["aqi"] != round(mean_of_aqi)


def test_hours_profile_counts_days(client, insert_measurement, frozen_now):
    """days — сколько разных суток окна дали данные в этот час; days_covered —
    сколько суток окна вообще с данными."""
    start, _ = _hours_window()
    for day in range(3):
        insert_measurement(ts=start + day * 86400 + 5 * 3600, pm25=10.0)
        insert_measurement(ts=start + day * 86400 + 5 * 3600 + 60, pm25=10.0)
    insert_measurement(ts=start + 86400 + 7 * 3600, pm25=10.0)  # те же вторые сутки

    d = client.get("/api/v1/hours").json()
    assert d["hours"][5]["n"] == 6 and d["hours"][5]["days"] == 3
    assert d["hours"][7]["n"] == 1 and d["hours"][7]["days"] == 1
    assert d["days_covered"] == 3


def test_hours_profile_rejects_bad_tz_offset(client):
    r = client.get("/api/v1/hours", params={"tz_offset": 900})
    assert r.status_code == 400
    assert "tz_offset must be within" in r.json()["detail"]


# --------------------------------------------------------------------------
# /healthz и статика
# --------------------------------------------------------------------------

def test_healthz_empty_db(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "last_ts": None, "rows": 0}


def test_healthz_counts_rows_and_last_ts(client, insert_measurement, frozen_now):
    insert_measurement(ts=frozen_now - 3600, pm25=1.0)
    insert_measurement(ts=frozen_now - 60, pm25=2.0)

    assert client.get("/healthz").json() == {
        "ok": True, "last_ts": frozen_now - 60, "rows": 2,
    }


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text
