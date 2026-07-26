"""Тесты HTTP-слоя server/main.py через fastapi.testclient.TestClient.

Фикстуры (client, auth_client, db_path, insert_measurement) и константы
PUSH_USER/PUSH_PASS живут в tests/conftest.py — здесь только тесты.

Время в тестах истории заморожено (фикстура frozen_now): иначе граница
календарных суток делала бы результат зависимым от момента запуска.
"""

import base64
from datetime import datetime, timezone

import pytest

import main
from conftest import PUSH_PASS, PUSH_USER

# Опорный момент: среда 2026-03-11 15:20:00 UTC. Среда — чтобы «неделя с
# понедельника» отличалась и от начала суток, и от начала месяца.
NOW = int(datetime(2026, 3, 11, 15, 20, 0, tzinfo=timezone.utc).timestamp())


def _ts(year, month, day, hour=0, minute=0):
    """Epoch-секунды для момента в UTC — чтобы не писать магические числа."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def frozen_now(monkeypatch):
    """Замораживает time.time(), который main зовёт в push/current/history."""
    monkeypatch.setattr(main.time, "time", lambda: float(NOW))
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
# period_start — чистая функция, тестируем без HTTP
# --------------------------------------------------------------------------

def test_period_start_day_utc():
    """Сутки в UTC: начало — полночь того же дня."""
    assert main.period_start(_ts(2026, 3, 11, 15, 20), "day", 0) == _ts(2026, 3, 11)


def test_period_start_day_positive_offset():
    """UTC+3: 00:30 UTC — это уже 03:30 по клиенту, значит сутки начались
    в 00:00 местных, то есть 2026-03-10 21:00 UTC."""
    now = _ts(2026, 3, 11, 0, 30)
    assert main.period_start(now, "day", 180) == _ts(2026, 3, 10, 21, 0)


def test_period_start_day_negative_offset():
    """UTC-5: 02:00 UTC — это ещё 21:00 предыдущего дня по клиенту, значит
    местные сутки начались в 2026-03-10 00:00 местных = 05:00 UTC."""
    now = _ts(2026, 3, 11, 2, 0)
    assert main.period_start(now, "day", -300) == _ts(2026, 3, 10, 5, 0)


def test_period_start_week_from_monday():
    """Неделя отсчитывается от понедельника: среда 11-го -> понедельник 9-е."""
    now = _ts(2026, 3, 11, 15, 20)
    assert main.period_start(now, "week", 0) == _ts(2026, 3, 9)


def test_period_start_week_with_offset_crosses_day():
    """UTC-5: 2026-03-09 01:00 UTC — по клиенту ещё воскресенье 8-е, значит
    неделя началась в понедельник 2-го (00:00 местных = 05:00 UTC)."""
    now = _ts(2026, 3, 9, 1, 0)
    assert main.period_start(now, "week", -300) == _ts(2026, 3, 2, 5, 0)


def test_period_start_month():
    """Месяц: первое число, полночь."""
    assert main.period_start(_ts(2026, 3, 11, 15, 20), "month", 0) == _ts(2026, 3, 1)


def test_period_start_month_with_offset_crosses_month():
    """UTC+3: 2026-03-01 01:00 UTC — по клиенту уже 1 марта 04:00, месяц
    начался в 00:00 местных = 2026-02-28 21:00 UTC."""
    now = _ts(2026, 3, 1, 1, 0)
    assert main.period_start(now, "month", 180) == _ts(2026, 2, 28, 21, 0)


@pytest.mark.parametrize("period", sorted(main.PERIODS))
@pytest.mark.parametrize("tz_offset", [-840, -300, 0, 180, 840])
def test_period_start_never_in_future(period, tz_offset):
    """Начало периода не может оказаться позже текущего момента."""
    assert main.period_start(NOW, period, tz_offset) <= NOW


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
    r = client.post("/api/v1/push", content=b"x" * (main.MAX_PUSH_BODY + 1))
    assert r.status_code == 413
    assert client.get("/healthz").json()["rows"] == 0


def test_push_body_at_limit_is_parsed(client):
    """Ровно MAX_PUSH_BODY байт — не «too large»: до разбора доходит, падает
    уже на JSON (нужен именно 400, а не 413)."""
    r = client.post("/api/v1/push", content=b"x" * main.MAX_PUSH_BODY)
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
    # Схема с маленькой буквы: креды верные, но main.py проверяет
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
    assert main.PUSH_PASS == ""
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


@pytest.mark.parametrize("period,bucket", [("day", 300), ("week", 1800), ("month", 7200)])
def test_history_bucket_per_period(client, period, bucket):
    d = client.get("/api/v1/history", params={"period": period}).json()
    assert d["period"] == period
    assert d["bucket_s"] == bucket


@pytest.mark.parametrize("tz_offset,status", [
    (840, 200), (841, 400), (-840, 200), (-841, 400), (0, 200),
])
def test_history_tz_offset_bounds(client, tz_offset, status):
    """MAX_TZ_OFFSET = ±840 минут, границы включены."""
    r = client.get("/api/v1/history", params={"period": "day", "tz_offset": tz_offset})
    assert r.status_code == status


@pytest.mark.parametrize("tz_offset,expected_start", [
    (0, (2026, 3, 11, 0, 0)),
    (180, (2026, 3, 10, 21, 0)),     # UTC+3: сутки клиента начались вчера в 21:00 UTC
    (-300, (2026, 3, 11, 5, 0)),     # UTC-5: и закончатся позже, чем сутки UTC
])
def test_history_start_follows_tz_offset(client, frozen_now, tz_offset, expected_start):
    """tz_offset должен доезжать до границы периода, а не только валидироваться.

    Ожидания — литералы, посчитанные вручную (те же, что в тестах period_start),
    а не вызов main.period_start: иначе тест повторил бы реализацию и не заметил,
    если бы history перестала передавать пояс дальше.
    """
    d = client.get("/api/v1/history",
                   params={"period": "day", "tz_offset": tz_offset}).json()
    assert d["start"] == _ts(*expected_start)


def test_history_buckets_are_anchored_to_period_start_not_epoch(
        client, insert_measurement, frozen_now):
    """Корзины отсчитываются от начала периода, а не от эпохи (CLAUDE.md).

    Кейс подобран так, чтобы две привязки расходились: period=month с
    tz_offset=330 (Индия, UTC+5:30) даёт start = 2026-02-28 18:30 UTC, а это
    не кратно корзине месяца в 7200 с. При эпохальной привязке измерение
    получило бы t, которого нет в сетке range(start, now+1, 7200), и молча
    пропало бы из выдачи.
    """
    start = main.period_start(frozen_now, "month", 330)
    assert start == _ts(2026, 2, 28, 18, 30)
    assert start % 7200 == 1800, "кейс бесполезен: сетки совпали, расхождение не поймать"

    insert_measurement(ts=start + 100, pm25=10.0, pm10=20.0)
    d = client.get("/api/v1/history",
                   params={"period": "month", "tz_offset": 330}).json()

    assert d["start"] == start
    assert d["points"][0]["t"] == start
    first = next(p for p in d["points"] if p["t"] == start)
    assert first["n"] == 1
    assert first["pm25"] == 10.0


def test_history_grid_is_dense_and_starts_at_period_start(client, frozen_now):
    """Сетка плотная: от period_start ровным шагом bucket_s, пустые корзины
    приходят с n=0 и null-метриками."""
    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()

    start = main.period_start(frozen_now, "day", 0)
    assert d["start"] == start == _ts(2026, 3, 11)
    assert d["end"] == frozen_now

    points = d["points"]
    assert points[0]["t"] == start
    assert [p["t"] for p in points] == list(range(start, frozen_now + 1, 300))
    # 15:20 от полуночи — ровно 184 полных корзины по 5 минут плюс нулевая
    assert len(points) == 185
    for p in points:
        assert p["n"] == 0
        assert p["pm25"] is None and p["pm10"] is None
        assert p["temperature"] is None and p["humidity"] is None
        assert p["pressure"] is None
        assert p["aqi"] is None


def test_history_measurement_lands_in_its_bucket(client, insert_measurement, frozen_now):
    start = main.period_start(frozen_now, "day", 0)
    ts = start + 3600 + 100  # 01:01:40 — середина корзины, начинающейся в 01:00
    insert_measurement(ts=ts, pm25=10.0, pm10=20.0, temperature=5.5)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    expected_t = start + ((ts - start) // 300) * 300
    assert expected_t == start + 3600

    filled = [p for p in d["points"] if p["n"] > 0]
    assert len(filled) == 1
    assert filled[0]["t"] == expected_t
    assert filled[0]["pm25"] == 10.0
    assert filled[0]["pm10"] == 20.0
    assert filled[0]["temperature"] == 5.5
    # мгновенный AQI корзины: pm25=10 доминирует над pm10=20,
    # 49/26.3*(10.0-9.1)+51 = 52.68 -> 53
    assert filled[0]["aqi"] == 53


def test_history_averages_within_bucket(client, insert_measurement, frozen_now):
    """Несколько измерений в одной корзине усредняются, n — их количество."""
    start = main.period_start(frozen_now, "day", 0)
    base = start + 7200  # 02:00
    insert_measurement(ts=base + 10, pm25=10.0, pm10=20.0)
    insert_measurement(ts=base + 200, pm25=30.0, pm10=41.0)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == base)
    assert point["n"] == 2
    assert point["pm25"] == 20.0
    assert point["pm10"] == 30.5


def test_history_ignores_measurements_before_period_start(client, insert_measurement,
                                                          frozen_now):
    """Точка вчерашним днём не попадает в суточную историю."""
    start = main.period_start(frozen_now, "day", 0)
    insert_measurement(ts=start - 60, pm25=99.0, pm10=99.0)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    assert all(p["n"] == 0 for p in d["points"])


def test_history_includes_measurement_exactly_at_period_start(
        client, insert_measurement, frozen_now):
    """Граница включающая: WHERE ts >= start, а не ts > start.

    Точка ровно в момент period_start обязана попасть в нулевую корзину —
    иначе первое измерение суток теряется каждый раз, когда датчик успел
    отправиться ровно в полночь.
    """
    start = main.period_start(frozen_now, "day", 0)
    insert_measurement(ts=start, pm25=10.0, pm10=20.0)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    assert d["points"][0]["t"] == start
    assert d["points"][0]["n"] == 1
    assert d["points"][0]["pm25"] == 10.0


def test_history_week_covers_previous_days(client, insert_measurement, frozen_now):
    """Недельный период начинается с понедельника и включает прошедшие сутки."""
    week_start = main.period_start(frozen_now, "week", 0)
    assert week_start == _ts(2026, 3, 9)
    insert_measurement(ts=week_start + 1800, pm25=12.0, pm10=15.0)

    d = client.get("/api/v1/history", params={"period": "week", "tz_offset": 0}).json()
    assert d["start"] == week_start
    point = next(p for p in d["points"] if p["t"] == week_start + 1800)
    assert point["n"] == 1
    assert point["pm25"] == 12.0


def test_history_counts_rows_not_values(client, insert_measurement, frozen_now):
    """n — число строк в корзине, а не число непустых значений метрики:
    корзина с одной лишь температурой всё равно приходит с n=1 и pm25=None.
    Поведение as-is, см. отчёт."""
    start = main.period_start(frozen_now, "day", 0)
    insert_measurement(ts=start + 600, temperature=7.0)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    point = next(p for p in d["points"] if p["t"] == start + 600)
    assert point["n"] == 1
    assert point["temperature"] == 7.0
    assert point["pm25"] is None
    assert point["aqi"] is None


def test_history_drops_measurements_newer_than_now(client, insert_measurement,
                                                   frozen_now):
    """Измерение с ts из будущего в выдачу не попадает: сетка обрывается на
    now, а SQL-корзина уезжает за неё. Поведение as-is, см. отчёт."""
    insert_measurement(ts=frozen_now + 3600, pm25=50.0, pm10=60.0)

    d = client.get("/api/v1/history", params={"period": "day", "tz_offset": 0}).json()
    assert d["points"][-1]["t"] <= d["end"]
    assert all(p["n"] == 0 for p in d["points"])
    # строка при этом в БД есть — потерялась именно выдача
    assert client.get("/healthz").json()["rows"] == 1


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
