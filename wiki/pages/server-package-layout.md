# Раскладка пакета `server/`

Бэкенд жил одним файлом `server/main.py` (314 строк на момент разбиения) и
импортировался как набор top-level модулей: `import main`, `import aqi`,
`pythonpath = server` в `pytest.ini`, `uvicorn main:app` в systemd-юните.

С переходом на мульти-тучковую версию это стало ограничителем: план внедрения
предполагает 16 последующих сессий, часть из которых идёт параллельно, и любые
две из них конфликтовали бы на каждом мерже в один и тот же `main.py`. Поэтому
`server/` сделан настоящим python-пакетом, а код разведён по файлам-владельцам.

## Кто чем владеет

| Модуль | Ответственность |
|---|---|
| `main.py` | Только сборка: `FastAPI()`, `lifespan`, CORS, `mount("/static")`, `include_router()`, `GET /` |
| `db.py` | `DB_PATH`, `connect()` и прагмы. Схемой **не** владеет |
| `migrations.py`, `migrate.py` | Версии схемы и раннер — [db-schema-migrations](db-schema-migrations.md) |
| `auth.py` | Креды push: `PUSH_USER`, `PUSH_PASS`, `check_push_auth()` |
| `history.py` | `PERIODS`, `MAX_TZ_OFFSET`, `period_window()`, `build_history()` |
| `aqi.py` | US EPA AQI — как был, чистые функции |
| `geo.py` | `public_coords()` — HMAC-смещение публичных координат |
| `quality.py` | Каркас: качество данных |
| `routes/push.py` | `FIELD_MAP`, `MAX_PUSH_BODY`, `POST /api/v1/push` |
| `routes/api.py` | `_nowcast_aqi()`, `/api/v1/current`, `/api/v1/history`, `/healthz` |

Смысл границ — не эстетика, а развод владельцев по будущим сессиям:
`history.py` отделён от `routes/api.py`, чтобы работа над агрегатами и работа
над API-контрактом не столкнулись; `routes/` заведён каталогом, чтобы туда
добавлялись `web.py` и `public.py` без правки существующих файлов.

`history.py` не зависит от FastAPI: валидация `period`/`tz_offset` и
`HTTPException` остались в обработчике. Тот же принцип, что у `aqi.py`
(требование A5) — модуль должен переиспользоваться ботом и клиентом.

## Обращаться через модуль, не через `from … import`

`db.DB_PATH`, `auth.PUSH_USER`, `auth.PUSH_PASS` читаются в глобалы на импорте
модуля. Тесты подменяют именно эти глобалы (`monkeypatch.setattr(db, "DB_PATH", …)`),
поэтому код обязан читать их **через модуль**:

```python
from .. import auth, db          # да
with closing(db.connect()) as conn: ...

from ..db import DB_PATH          # нет: monkeypatch перестанет действовать
```

Ловушка тихая: при `from … import` авторизация push в тестах молча выключится,
и тесты на подделку кредов начнут проходить по неверной причине.

Исключение сделано для `routes/api.py`: `PERIODS`, `MAX_TZ_OFFSET` и
`build_history` импортируются именами. Причина — обработчик называется
`history()`, и это имя попадает в `operationId` OpenAPI; импорт модуля под
именем `history` его бы затенил, а переименование обработчика изменило бы
публичную схему. Константы и чистая функция никем не подменяются, так что
ограничение не нарушается.

## Ловушка деплоя: `--delete` не чистит уровень выше

`rsync --delete` удаляет лишнее **только внутри передаваемых каталогов**. Ни
`rsync --delete server/ …/app/server/`, ни `rsync --delete server …/app/` не
трогают файлы, лежащие прямо в `app/` — то есть старая плоская раскладка
(`main.py`, `aqi.py`, `static/` в корне `app/`) осталась бы на сервере навсегда.
Проверено эмпирически, не выведено из документации.

Рабочая команда передаёт корень репозитория с фильтром «только `server/`»:

```bash
rsync -az --delete --delete-excluded \
      --exclude '__pycache__' --include '/server/' --include '/server/**' --exclude '*' \
      ./ "$HOST":/opt/tu4ka/app/
```

`--delete` теперь работает на уровне `app/`, а `--delete-excluded` снимает
защиту с исключённых файлов на приёмнике и вычищает всё, чего в `server/` нет.
Порядок правил важен — rsync применяет первое совпавшее, поэтому
`--exclude '__pycache__'` стоит **до** include-ов. Инвариант, который держит эта
команда: **`/opt/tu4ka/app/` содержит ровно `server/`**.

БД (`/var/lib/tu4ka`), venv (`/opt/tu4ka/venv`) и `/opt/tu4ka/deploy` лежат вне
`app/` и под фильтр не попадают.

## Запуск

- systemd: `uvicorn server.main:app --app-dir /opt/tu4ka/app`. `--app-dir` задан
  явно, чтобы не зависеть от того, кладёт ли uvicorn `WorkingDirectory` в
  `sys.path`; проверено запуском из чужого cwd.
- локальный стенд: `.venv/bin/python -m uvicorn server.main:app --port 8765`
  из корня репозитория.
- тесты: `pythonpath = .` в `pytest.ini`, импорты вида `from server import …`.
- CI: `python -m compileall -q server` вместо `py_compile server/*.py` — glob по
  одному уровню не видел бы `server/routes/`.

## Что осталось за рамками

Разбиение — чистый рефакторинг: поведение не менялось, `/openapi.json` совпадает
с версией до разбиения байт в байт. `server/static/index.html` не тронут.

Дальнейшим шагом схема уехала из `lifespan` в раннер миграций
(`migrate.py`/`migrations.py`, см. [db-schema-migrations](db-schema-migrations.md)),
и `geo.py` наполнился. Пустым каркасом остаётся только `quality.py`.

См. также: [deploy-cicd](deploy-cicd.md), [history-buckets](history-buckets.md),
[protocol-airrohr-push](protocol-airrohr-push.md), [roadmap](roadmap.md).
