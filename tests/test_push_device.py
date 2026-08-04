"""Приём, маршрутизированный по устройству: креды, chip_id, лимит.

Фикстуры (client, auth_client, device_client, add_device, set_push_secret,
db_path) и константы PUSH_USER/PUSH_PASS живут в tests/conftest.py.

Разница двух путей авторизации: `device_client` — секрет лежит в devices
(нормальный режим), `auth_client` — секрета в БД нет, но задан в окружении
(мост, см. server/auth.py).
"""

import logging
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from conftest import PUSH_PASS, PUSH_USER
from server import auth, main
from server.routes import push as push_routes


def _body(esp8266id="2998975", **values):
    return {
        "esp8266id": esp8266id,
        "sensordatavalues": [
            {"value_type": k, "value": str(v)} for k, v in values.items()
        ],
    }


def read(db_path, sql, args=()):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# маршрутизация по кредам
# --------------------------------------------------------------------------

def test_push_lands_on_the_device_from_credentials(device_client, db_path):
    r = device_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                           auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 200, r.text
    assert read(db_path, "SELECT device_id, pm25 FROM measurements") == [(1, 7.1)]


def test_second_device_writes_under_its_own_id(device_client, add_device, db_path):
    other = add_device("сосед", "d2", "секрет-соседа", chip_id="777")

    assert device_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                              auth=(PUSH_USER, PUSH_PASS)).status_code == 200
    assert device_client.post("/api/v1/push", json=_body("777", SDS_P2="9.9"),
                              auth=("d2", "секрет-соседа")).status_code == 200

    rows = read(db_path, "SELECT device_id, pm25 FROM measurements ORDER BY device_id")
    assert rows == [(1, 7.1), (other, 9.9)]


def test_credentials_of_one_device_do_not_open_another(device_client, add_device,
                                                       db_path):
    add_device("сосед", "d2", "секрет-соседа")

    r = device_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                           auth=("d2", PUSH_PASS))

    assert r.status_code == 401
    assert read(db_path, "SELECT count(*) FROM measurements")[0][0] == 0


def test_wrong_password_writes_nothing(device_client, db_path):
    r = device_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                           auth=(PUSH_USER, PUSH_PASS + "x"))

    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"
    assert read(db_path, "SELECT count(*) FROM measurements")[0][0] == 0


def test_unknown_user_writes_nothing(device_client, db_path):
    r = device_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                           auth=("никто", PUSH_PASS))

    assert r.status_code == 401
    assert read(db_path, "SELECT count(*) FROM measurements")[0][0] == 0


def test_missing_header_is_401_when_the_device_has_a_secret(device_client):
    assert device_client.post("/api/v1/push", json=_body()).status_code == 401


# --------------------------------------------------------------------------
# ротация секрета: grace-период
# --------------------------------------------------------------------------

def test_previous_secret_works_until_it_expires(db_path, set_push_secret):
    """Перенастройка датчика идёт руками, между сменой секрета на сервере и
    в прошивке проходит время. Без окна это была бы дыра в данных."""
    set_push_secret("новый", prev="старый", prev_until=int(time.time()) + 3600)

    with TestClient(main.app) as c:
        assert c.post("/api/v1/push", json=_body(SDS_P2="1.0"),
                      auth=(PUSH_USER, "старый")).status_code == 200
        assert c.post("/api/v1/push", json=_body(SDS_P2="2.0"),
                      auth=(PUSH_USER, "новый")).status_code == 200


def test_previous_secret_stops_working_after_the_grace_period(db_path,
                                                              set_push_secret):
    set_push_secret("новый", prev="старый", prev_until=int(time.time()) - 1)

    with TestClient(main.app) as c:
        assert c.post("/api/v1/push", json=_body(),
                      auth=(PUSH_USER, "старый")).status_code == 401
        assert c.post("/api/v1/push", json=_body(),
                      auth=(PUSH_USER, "новый")).status_code == 200


# --------------------------------------------------------------------------
# мост на переменные окружения
# --------------------------------------------------------------------------

def test_env_credentials_still_work_when_db_has_no_secret(auth_client, db_path):
    """Расхождение БД и /etc/<среда>/env не должно ронять доставку данных:
    прошивка не переспрашивает, 401 — это потерянные навсегда измерения."""
    r = auth_client.post("/api/v1/push", json=_body(SDS_P2="7.1"),
                         auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 200, r.text
    assert read(db_path, "SELECT device_id FROM measurements") == [(1,)]
    assert read(db_path, "SELECT push_secret_sha256 FROM devices") == [(None,)]


def test_db_secret_wins_over_the_env_bridge(db_path, set_push_secret, monkeypatch):
    """Секрет в БД есть — env перестаёт быть словом в этом разговоре."""
    set_push_secret("из-базы")
    monkeypatch.setattr(auth, "PUSH_USER", PUSH_USER)
    monkeypatch.setattr(auth, "PUSH_PASS", "из-окружения")

    with TestClient(main.app) as c:
        assert c.post("/api/v1/push", json=_body(),
                      auth=(PUSH_USER, "из-окружения")).status_code == 401
        assert c.post("/api/v1/push", json=_body(),
                      auth=(PUSH_USER, "из-базы")).status_code == 200


def test_startup_stays_quiet_when_the_secret_lives_in_the_db(db_path,
                                                             set_push_secret,
                                                             caplog):
    """Пустой TU4KA_PUSH_PASS — нормальное состояние, когда хеши в devices.
    Предупреждение «принимаем без авторизации» в этом случае было бы враньём."""
    set_push_secret(PUSH_PASS)

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        with TestClient(main.app):
            pass

    assert "без авторизации" not in caplog.text


def test_startup_warns_when_there_is_no_secret_anywhere(db_path, caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        with TestClient(main.app):
            pass

    assert "без авторизации" in caplog.text


# --------------------------------------------------------------------------
# chip_id — справочно, маршрутизацию не решает
# --------------------------------------------------------------------------

def test_first_push_fills_an_empty_chip_id(device_client, db_path):
    assert read(db_path, "SELECT chip_id FROM devices") == [(None,)]

    device_client.post("/api/v1/push", json=_body("2998975"),
                       auth=(PUSH_USER, PUSH_PASS))

    assert read(db_path, "SELECT chip_id, chip_id_conflict_at FROM devices") == [
        ("2998975", None)]


def test_foreign_chip_id_is_flagged_but_accepted(device_client, db_path):
    """Человек мог перепаять плату: отклонять такой push значило бы терять
    данные из-за смены железа. Помечаем и принимаем."""
    device_client.post("/api/v1/push", json=_body("2998975"),
                       auth=(PUSH_USER, PUSH_PASS))

    r = device_client.post("/api/v1/push", json=_body("111", SDS_P2="7.1"),
                           auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 200
    chip_id, conflict_at = read(
        db_path, "SELECT chip_id, chip_id_conflict_at FROM devices")[0]
    assert chip_id == "2998975"      # не перезаписан чужим
    assert conflict_at is not None
    # измерение записано (оба push пришлись на одну секунду, второй перезаписал
    # первый по ON CONFLICT — это штатное поведение приёма, см. P10)
    assert read(db_path, "SELECT pm25 FROM measurements") == [(7.1,)]


def test_conflict_mark_answers_when_we_noticed_not_when_last_seen(device_client,
                                                                  db_path):
    device_client.post("/api/v1/push", json=_body("2998975"),
                       auth=(PUSH_USER, PUSH_PASS))
    device_client.post("/api/v1/push", json=_body("111"),
                       auth=(PUSH_USER, PUSH_PASS))
    first = read(db_path, "SELECT chip_id_conflict_at FROM devices")[0][0]

    time.sleep(1.1)
    device_client.post("/api/v1/push", json=_body("222"),
                       auth=(PUSH_USER, PUSH_PASS))

    assert read(db_path, "SELECT chip_id_conflict_at FROM devices")[0][0] == first


def test_chip_id_never_routes_the_push(device_client, add_device, db_path):
    """Чужой chip_id в теле не уводит измерение на устройство с таким chip_id:
    маршрут решают только креды."""
    other = add_device("сосед", "d2", "секрет-соседа", chip_id="777")

    device_client.post("/api/v1/push", json=_body("777", SDS_P2="7.1"),
                       auth=(PUSH_USER, PUSH_PASS))

    assert read(db_path, "SELECT device_id FROM measurements") == [(1,)]
    assert read(db_path, "SELECT chip_id FROM devices WHERE id = ?",
                (other,)) == [("777",)]


# --------------------------------------------------------------------------
# rate limit
# --------------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """Управляемое время: push зовёт time.time() и для метки, и для окна."""

    class Clock:
        now = 1_800_000_000.0

        def tick(self, seconds):
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(time, "time", lambda: c.now)
    return c


def test_fourth_push_in_a_minute_is_rejected(device_client, db_path, clock):
    for i in range(push_routes.RATE_LIMIT_PER_MIN):
        clock.tick(1)
        assert device_client.post("/api/v1/push", json=_body(SDS_P2=str(i)),
                                  auth=(PUSH_USER, PUSH_PASS)).status_code == 200

    clock.tick(1)
    r = device_client.post("/api/v1/push", json=_body(SDS_P2="9.9"),
                           auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 429
    assert r.headers.get("Retry-After") == str(push_routes.RATE_WINDOW_S)
    rows = read(db_path, "SELECT count(*) FROM measurements")[0][0]
    assert rows == push_routes.RATE_LIMIT_PER_MIN


def test_the_window_slides(device_client, clock):
    """Датчик шлёт раз в 60 с — лимит не должен превращаться в потолок за час."""
    for _ in range(push_routes.RATE_LIMIT_PER_MIN):
        clock.tick(1)
        device_client.post("/api/v1/push", json=_body(),
                           auth=(PUSH_USER, PUSH_PASS))

    clock.tick(push_routes.RATE_WINDOW_S)
    r = device_client.post("/api/v1/push", json=_body(),
                           auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 200, r.text


def test_the_limit_is_per_device(device_client, add_device, clock):
    add_device("сосед", "d2", "секрет-соседа")
    for _ in range(push_routes.RATE_LIMIT_PER_MIN + 1):
        clock.tick(1)
        device_client.post("/api/v1/push", json=_body(),
                           auth=(PUSH_USER, PUSH_PASS))

    r = device_client.post("/api/v1/push", json=_body(),
                           auth=("d2", "секрет-соседа"))

    assert r.status_code == 200, r.text


def test_rejected_pushes_do_not_eat_the_limit(device_client, clock):
    """Считаем принятые записи: чужой перебор пароля не должен выедать окно
    у самого датчика."""
    for _ in range(10):
        clock.tick(1)
        device_client.post("/api/v1/push", json=_body(),
                           auth=(PUSH_USER, "не-тот-пароль"))

    clock.tick(1)
    r = device_client.post("/api/v1/push", json=_body(),
                           auth=(PUSH_USER, PUSH_PASS))

    assert r.status_code == 200, r.text
