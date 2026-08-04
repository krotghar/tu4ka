#!/usr/bin/env python3
"""Алерт о замолчавшем датчике + внешний dead-man's switch.

Запускается таймером tu4ka-alert.timer раз в 5 минут. Делает две независимые
вещи:

1. Смотрит, давно ли приходило последнее измерение, и пишет в телеграм, если
   тишина затянулась. Умеет молчать в пределах cooldown и сообщать
   о восстановлении ровно один раз.
2. Проверяет /healthz и ТОЛЬКО при успехе пингует healthchecks.io. Порядок
   принципиален: безусловный пинг проверял бы живость таймера, а не сервиса.

ТОЛЬКО ПРОД. У беты база — замороженный снимок, MAX(ts) в ней не обновляется
никогда, и алерт там срабатывал бы вечно.

Решающая часть — чистые функции silence_verdict/should_notify, они покрыты
tests/test_alert.py. Всё, что ниже них, — ввод-вывод.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DB = os.environ.get("TU4KA_DB", "/var/lib/tu4ka/tu4ka.db")
STATE = os.environ.get("TU4KA_ALERT_STATE", "/var/lib/tu4ka/alert-state.json")
HEALTHZ = os.environ.get("TU4KA_HEALTHZ_URL", "http://127.0.0.1:8000/healthz")
THRESHOLD_S = int(os.environ.get("TU4KA_SILENCE_THRESHOLD_S", 3600))
COOLDOWN_S = int(os.environ.get("TU4KA_ALERT_COOLDOWN_S", 6 * 3600))


# --- решающая часть: чистые функции, покрыты тестами ------------------------

def silence_verdict(last_ts, now, threshold_s):
    """"ok" или "silent". Пустая база — тоже silent: датчик не доставляет."""
    if last_ts is None:
        return "silent"
    return "silent" if now - last_ts > threshold_s else "ok"


def should_notify(verdict, state, now, cooldown_s):
    """Решает, слать ли сообщение. Возвращает (вид | None, новое состояние).

    Виды: "silent" — датчик молчит, "recovered" — данные снова пошли.

    Сообщение о восстановлении обязательно: без него единственным признаком
    «всё починилось» была бы тишина в чате, а тишина уже занята под другой
    смысл. Cooldown нужен, чтобы обесточенный на неделю датчик не прислал
    две тысячи сообщений.
    """
    was = state.get("verdict", "ok")
    notified_at = state.get("notified_at", 0)

    if verdict == "silent":
        if was != "silent" or now - notified_at >= cooldown_s:
            return "silent", {"verdict": "silent", "notified_at": now}
        return None, {"verdict": "silent", "notified_at": notified_at}

    if was == "silent":
        return "recovered", {"verdict": "ok", "notified_at": now}
    return None, {"verdict": "ok", "notified_at": notified_at}


def format_message(kind, last_ts, now):
    """Текст сообщения. Отдельно от отправки, чтобы был проверяем."""
    if last_ts is None:
        return "⚠️ tu4ka: в базе нет ни одного измерения"
    ago = int(now - last_ts)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last_ts))
    if kind == "silent":
        return (f"⚠️ tu4ka: датчик молчит {ago // 60} мин\n"
                f"последнее измерение — {when}")
    return (f"✅ tu4ka: данные снова идут\n"
            f"последнее измерение — {when} ({ago} с назад)")


# --- ввод-вывод --------------------------------------------------------------

def read_last_ts(path):
    """MAX(ts) из базы. mode=ro — тот же инвариант, что у бэкапа и пересева.

    Запрос намеренно без device_id: таблицы devices ещё нет, она появится
    в S1. Обобщать на схему, которой не существует, — гадание.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT MAX(ts) FROM measurements").fetchone()[0]
    finally:
        con.close()


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def send_telegram(token, chat_id, text):
    """Отправка в телеграм.

    Исключения urllib несут в тексте URL, а в URL телеграма зашит токен бота —
    поэтому наружу отдаётся только код ошибки, без самого исключения. Иначе
    токен утёк бы в journalctl, который читается без особых прав.
    """
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15):
            return True
    except urllib.error.HTTPError as e:
        print(f"телеграм: HTTP {e.code}", file=sys.stderr)
    except Exception as e:  # сеть, DNS, таймаут
        print(f"телеграм: {type(e).__name__}", file=sys.stderr)
    return False


def healthz_ok(url, attempts=3, delay=2):
    """Живо ли приложение. Требуем "ok" в теле, а не код 200: 301 тоже «успех»
    для клиента, и редирект на месте /healthz прошёл бы как здоровье.

    Несколько попыток — потому что во время выкладки запрос честно ждёт
    в очереди ядра, и одиночная проверка приняла бы ожидание за падение.
    """
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if '"ok"' in r.read(4096).decode("utf-8", "replace"):
                    return True
        except Exception:
            pass
        if i + 1 < attempts:
            time.sleep(delay)
    return False


def ping(url):
    """Пинг dead-man's switch. URL сам по себе секрет — в лог его не пишем."""
    try:
        with urllib.request.urlopen(url, timeout=10):
            return True
    except Exception as e:
        print(f"heartbeat: {type(e).__name__}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test", action="store_true",
                    help="послать тестовое сообщение и выйти")
    args = ap.parse_args()

    token = os.environ.get("TU4KA_TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TU4KA_TG_CHAT_ID", "")
    hc_url = os.environ.get("TU4KA_HC_URL", "")
    now = time.time()

    if args.test:
        if not token or not chat_id:
            sys.exit("телеграм не настроен: TU4KA_TG_BOT_TOKEN/TU4KA_TG_CHAT_ID пусты")
        ok = send_telegram(token, chat_id, "🔧 tu4ka: проверка канала уведомлений")
        print("тестовое сообщение отправлено" if ok else "отправить не удалось")
        return 0 if ok else 1

    # --- тишина датчика ---
    if token and chat_id:
        try:
            last_ts = read_last_ts(DB)
        except sqlite3.Error as e:
            # Не пингуем heartbeat: пусть внешний switch и сработает.
            print(f"база недоступна: {e}", file=sys.stderr)
            return 1
        verdict = silence_verdict(last_ts, now, THRESHOLD_S)
        kind, state = should_notify(verdict, load_state(STATE), now, COOLDOWN_S)
        if kind:
            text = format_message(kind, last_ts, now)
            print(text.replace("\n", "; "))
            if not send_telegram(token, chat_id, text):
                # Состояние не двигаем: на следующем прогоне попробуем снова,
                # иначе неотправленное сообщение считалось бы доставленным.
                return 1
        save_state(STATE, state)
    else:
        print("телеграм не настроен — проверка тишины пропущена")

    # --- dead-man's switch ---
    if hc_url:
        if healthz_ok(HEALTHZ):
            ping(hc_url)
        else:
            print("healthz не отвечает — heartbeat намеренно не послан", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
