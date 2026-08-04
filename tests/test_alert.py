"""Тесты решающей части алерта (deploy/alert.py).

`deploy/` импортируется как namespace-пакет (PEP 420) — `pythonpath = .` из
pytest.ini кладёт корень репозитория в sys.path, `__init__.py` не нужен.

Покрыты только чистые функции. Отправка в телеграм, пинг heartbeat и чтение
базы — ввод-вывод, он проверяется руками на живом сервере (см.
wiki/pages/alerting.md).
"""

from deploy import alert

HOUR = 3600


# --- silence_verdict ---------------------------------------------------------

def test_fresh_measurement_is_ok():
    now = 1_000_000
    assert alert.silence_verdict(now - 60, now, HOUR) == "ok"


def test_stale_measurement_is_silent():
    now = 1_000_000
    assert alert.silence_verdict(now - 2 * HOUR, now, HOUR) == "silent"


def test_exactly_at_threshold_is_still_ok():
    """Граница не срабатывает: порог — «больше часа», а не «час и ровно»."""
    now = 1_000_000
    assert alert.silence_verdict(now - HOUR, now, HOUR) == "ok"


def test_empty_database_counts_as_silent():
    """MAX(ts) по пустой таблице — None. Данных нет, значит датчик не доставляет."""
    assert alert.silence_verdict(None, 1_000_000, HOUR) == "silent"


# --- should_notify -----------------------------------------------------------

def test_first_silence_notifies():
    kind, state = alert.should_notify("silent", {}, 1_000, HOUR)
    assert kind == "silent"
    assert state == {"verdict": "silent", "notified_at": 1_000}


def test_repeated_silence_stays_quiet_within_cooldown():
    """Обесточенный на неделю датчик не должен слать сообщение каждые 5 минут."""
    state = {"verdict": "silent", "notified_at": 1_000}
    kind, new = alert.should_notify("silent", state, 1_000 + HOUR, 6 * HOUR)
    assert kind is None
    assert new["notified_at"] == 1_000, "время последней отправки не должно сдвигаться"


def test_silence_repeats_after_cooldown():
    state = {"verdict": "silent", "notified_at": 1_000}
    kind, new = alert.should_notify("silent", state, 1_000 + 6 * HOUR, 6 * HOUR)
    assert kind == "silent"
    assert new["notified_at"] == 1_000 + 6 * HOUR


def test_recovery_notifies_once():
    """Сообщение о восстановлении обязательно: иначе единственным признаком
    «починилось» была бы тишина в чате, а тишина уже означает другое."""
    state = {"verdict": "silent", "notified_at": 1_000}
    kind, new = alert.should_notify("ok", state, 2_000, 6 * HOUR)
    assert kind == "recovered"
    assert new["verdict"] == "ok"

    kind, _ = alert.should_notify("ok", new, 3_000, 6 * HOUR)
    assert kind is None, "второй раз о восстановлении не сообщаем"


def test_steady_ok_is_silent():
    state = {"verdict": "ok", "notified_at": 0}
    assert alert.should_notify("ok", state, 10_000, 6 * HOUR)[0] is None


def test_cooldown_does_not_suppress_recovery():
    """Восстановление важнее cooldown: оно приходит сразу, даже если сообщение
    о тишине было только что."""
    state = {"verdict": "silent", "notified_at": 999}
    kind, _ = alert.should_notify("ok", state, 1_000, 6 * HOUR)
    assert kind == "recovered"


# --- format_message ----------------------------------------------------------

def test_silent_message_reports_how_long():
    text = alert.format_message("silent", 1_000_000 - 90 * 60, 1_000_000)
    assert "90 мин" in text


def test_message_for_empty_database_does_not_lie_about_time():
    text = alert.format_message("silent", None, 1_000_000)
    assert "нет ни одного измерения" in text
