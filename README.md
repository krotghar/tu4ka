# tu4ka

Собственный сервис приёма и хранения данных домашнего датчика воздуха «тучка» —
стандартного airRohr (прошивка NRZ-2024-135/RU, ID 2998975) с SDS011 (пыль PM2.5/PM10)
и BME280 (температура, влажность, давление). Заменяет умершее родное облако
(api.beta.armaqi.org). Параллельно датчик продолжает слать данные на
sensor.community и madavi.de.

## Архитектура

```
[Датчик 192.168.27.8] --> [Сервер 178.160.230.131] --> [Веб/API]
```

- Датчик (SDS011 + BME280) каждые 60 секунд отправляет данные на сервер по HTTP.
- Сервер: VPS Ubuntu 24.04, FastAPI + uvicorn на порту 80.
- Хранилище: SQLite `/var/lib/tu4ka/tu4ka.db` (WAL).

## Формат данных от датчика

JSON вида:
```json
{
  "esp8266id":"2998975",
  "software_version":"NRZ-2024-135",
  "sensordatavalues": [
    {"value_type":"SDS_P1","value":"9.82"},
    ...
  ]
}
```

Маппинг:
- SDS_P1 → pm10
- SDS_P2 → pm25
- BME280_temperature/humidity/pressure (давление в Па, храним в гПа)
- BMP280_* тоже поддержаны

Push защищён HTTP basic auth.

## Endpoints

| Endpoint               | Метод | Описание                              |
|------------------------|-------|---------------------------------------|
| `/api/v1/push`         | POST  | Приём измерений от датчика            |
| `/api/v1/current`      | GET   | Последнее измерение + age_s           |
| `/api/v1/history`      | GET   | `?period=day\|week\|month` — корзины 5 мин / 30 мин / 2 ч |
| `/healthz`             | GET   | Статус и число строк                  |
| `/`                    | GET   | Веб-морда (графики, таблица)          |
| `/docs`                | GET   | OpenAPI-документация                  |

## Структура репозитория

- `server/main.py` — приложение FastAPI
- `server/static/index.html` — веб-морда
- `server/static/chart.umd.js` — Chart.js 4.4.9
- `deploy/deploy.sh` — скрипт деплоя
- `deploy/remote_setup.sh` — идемпотентная настройка сервера
- `deploy/tu4ka.service` — systemd-юнит
- `deploy/requirements.txt` — зависимости

## Деплой и эксплуатация

- Доступ: ssh-алиас `tu4ka` (root@178.160.230.131, ключ `~/.ssh/tu4ka`)
- Деплой: `./deploy/deploy.sh`
- Логи: `ssh tu4ka journalctl -u tu4ka -f`
- Код на сервере: `/opt/tu4ka/app`
- Виртуальное окружение: `/opt/tu4ka/venv`
- Креды push: `/etc/tu4ka/env`

## Настройка датчика

Веб-интерфейс датчика http://192.168.27.8/:
- Конфигурация → «Отправка данных на собственный API»
- Включить, Сервер 178.160.230.131, Путь `/api/v1/push`, Порт 80
- Пользователь `tu4ka`, Пароль — `TU4KA_PUSH_PASS` из `/etc/tu4ka/env`

## Планы

- REST API для Android-клиента (база уже есть)
- Телеграм-бот с уведомлениями о плохом качестве воздуха
