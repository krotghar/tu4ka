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
.venv/bin/python -m pytest -q           # тесты (venv из requirements-dev.txt)
./deploy/deploy.sh                      # ручной деплой (rsync + remote_setup.sh)
ssh tu4ka journalctl -u tu4ka -f        # логи сервиса
ssh tu4ka systemctl status tu4ka        # состояние сервиса
curl http://178.160.230.131/healthz     # быстрая проверка живости
gh run list --limit 5                   # прогоны CI
```

**Деплой автоматический**: push в `main` запускает `.github/workflows/ci.yml` —
джоб `test` (py_compile + pytest), затем `deploy`, который зовёт тот же
`deploy/deploy.sh` с `TU4KA_HOST=root@178.160.230.131`. Красные тесты деплой не
пускают. `deploy.sh` остаётся для ручного/аварийного прогона.

Линтера нет. Тесты — pytest, лежат в `tests/`, 222 штуки.

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
- `tests/` — pytest. `conftest.py` подменяет `main.DB_PATH`/`PUSH_*` через
  `setattr`, а не переменными окружения: `main.py` читает их в глобалы на импорте,
  так что `setenv` опаздывает. `TestClient` — только как контекст-менеджер, иначе
  не отработает `lifespan` и в БД не будет схемы. Время в тестах истории заморожено
  фикстурой `frozen_now` — без неё они плавают у границы суток.
- `.github/workflows/ci.yml` — CI/CD. Ключ деплоя лежит в секрете репозитория
  `TU4KA_SSH_KEY` (отдельный ed25519, в `authorized_keys` сервера с префиксом
  `restrict`), host key сервера запиннен прямо в workflow — он публичный, и в git
  его видно под ревью.

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
- Корзины `/history` привязаны к `period_start`, а не к эпохе. Инвариант закрыт
  тестом `test_history_buckets_are_anchored_to_period_start_not_epoch`: он специально
  берёт `period=month, tz_offset=330`, где две привязки расходятся. На `tz_offset=0`
  они совпадают, и регрессию не поймать — не «упрощать» кейс до UTC.
