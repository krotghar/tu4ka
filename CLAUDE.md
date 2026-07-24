# CLAUDE.md

Заметки для Claude Code по проекту tu4ka.

## Обзор

Сервис приёма и хранения данных датчика воздуха «тучка» — это стандартный
airRohr (прошивка NRZ-2024-135/RU, ID 2998975): SDS011 (пыль PM2.5/PM10) +
BME280 (температура, влажность, давление). Заменяет умершее родное облако
(api.beta.armaqi.org).

- Датчик: `http://192.168.27.8/` в локальной сети; каждые 60 с сам POST-ит
  измерение штатным механизмом «отправка на собственный API» (basic auth).
  Параллельно шлёт на sensor.community и madavi.de.
- Сервер: VPS `178.160.230.131` (Ubuntu 24.04, 2 ГБ RAM), ssh-алиас `tu4ka`
  (root, ключ `~/.ssh/tu4ka`). FastAPI + uvicorn на порту 80, systemd-юнит
  `tu4ka`, SQLite `/var/lib/tu4ka/tu4ka.db` (WAL).
- Веб-морда: `http://178.160.230.131/`; API: `/api/v1/current`,
  `/api/v1/history?period=day|week|month&tz_offset=<мин>`, `/healthz`,
  OpenAPI — `/docs`. Периоды календарные (с начала суток/недели/месяца),
  а не скользящие; `tz_offset` — пояс клиента, минут к востоку от UTC,
  по умолчанию 0 (UTC).

Планы: REST API для Android-клиента (база — `/api/v1/*`), телеграм-бот
с уведомлениями о плохом воздухе.

## Команды

```bash
./deploy/deploy.sh                      # деплой на сервер (rsync + remote_setup.sh)
ssh tu4ka journalctl -u tu4ka -f        # логи сервиса
ssh tu4ka systemctl status tu4ka        # состояние сервиса
curl http://178.160.230.131/healthz     # быстрая проверка живости
```

Тестов и линтера пока нет; python-код проверяется `python3 -m py_compile`.

## Архитектура

- `server/main.py` — всё приложение FastAPI: приём push (`POST /api/v1/push`),
  маппинг `sensordatavalues` (SDS_P1→pm10, SDS_P2→pm25, BME280_*/BMP280_*;
  давление приходит в Па, храним в гПа), SQLite-схема, history-агрегация.
  Корзины `/history` отсчитываются от начала периода (`period_start`), а не от
  эпохи: иначе первая корзина обрезана и даёт выброс в начале графика. Корзины
  без измерений отдаются с null и `n=0` — плотная сетка, ось времени клиента.
- `server/aqi.py` — расчёт US EPA AQI: breakpoints PM2.5/PM10 (редакция 2024,
  источник — AirNow Technical Assistance Document, Table 6) + NowCast (12-часовое
  взвешенное среднее). Чистые функции без зависимостей; `/current` отдаёт NowCast-AQI,
  `/history` — мгновенный AQI по корзине. Переиспользуется будущими ботом/клиентом.
- `server/static/index.html` — веб-морда одной страницей (Chart.js 4 вендорен
  в `server/static/chart.umd.js`, тёмная/светлая тема через prefers-color-scheme).
  AQI-плитка (официальные цвета категорий EPA) + тренды показателей за период
  (считаются на клиенте из `/history`).
- `deploy/remote_setup.sh` — идемпотентная настройка сервера; креды push
  генерируются один раз в `/etc/tu4ka/env` (TU4KA_PUSH_USER/TU4KA_PUSH_PASS).
- Код на сервере: `/opt/tu4ka/app`, venv `/opt/tu4ka/venv`.

Чего не ломать:

- Формат `POST /api/v1/push` — это формат прошивки airRohr, менять нельзя;
  датчик настроен на путь `/api/v1/push`, порт 80, user `tu4ka`.
- Пароль push в `/etc/tu4ka/env` должен совпадать с прошитым в датчике
  (веб-конфиг датчика → «Отправка данных на собственный API»).
- `measurements.raw` хранит исходный JSON — не удалять, это страховка
  для будущих миграций.
- БД лежит вне `/opt/tu4ka` — деплой с `--delete` её не трогает.
- Breakpoints AQI в `server/aqi.py` — из авторитетной таблицы AirNow (EPA), редакция
  2024. Не подгонять «по памяти»: при правках сверяться с первоисточником и держать
  самотест (эталонные точки: PM2.5 12.0→56, 35.4→100; PM10 155→101).
