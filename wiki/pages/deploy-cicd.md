# Деплой и CI/CD

## Инфраструктура

- Сервер: VPS `178.160.230.131`, Ubuntu 24.04, 2 ГБ RAM. ssh-алиас `tu4ka`
  (root, ключ `~/.ssh/tu4ka`).
- Домен `amqi.am`: `amqi.am`/`www.amqi.am` — сайт, `push.amqi.am` — приём от
  датчика, `beta.amqi.am` — бета-среда. Разводит их nginx по `server_name`.
  См. [domain-dns](domain-dns.md), [nginx-tls-beta](nginx-tls-beta.md).
- Снаружи слушает nginx (80 и 443). Приложения — только loopback: `tu4ka` на
  `127.0.0.1:8000`, `tu4ka-beta` на `127.0.0.1:8001`, оба через socket
  activation (`ExecStart=… uvicorn server.main:app --fd 3`), так что выкладка
  не роняет запросы.
- **Две среды на одном VPS**, всё выводится из `TU4KA_ENV` (`prod`|`beta`):
  корень `/opt/<имя>`, env `/etc/<имя>/env`, БД `/var/lib/<имя>/tu4ka.db`,
  юниты `<имя>.{service,socket}`.
- SQLite лежит **вне** корня среды, так что деплой с `--delete` её не задевает.
  БД беты пересевается снимком прода на каждом бета-деплое.
- Код на сервере: `/opt/<имя>/app/server/`, venv `/opt/<имя>/venv`. Инвариант
  «`app/` содержит ровно `server/`» держит rsync-фильтр в `deploy.sh` —
  см. [server-package-layout](server-package-layout.md).
- Креды push (`TU4KA_PUSH_USER`/`TU4KA_PUSH_PASS`) генерируются один раз в
  `/etc/<имя>/env` — идемпотентно, `deploy/remote_setup.sh` их не перегенерит
  при повторных запусках. У беты свой независимый пароль.
- Версии зависимостей в `deploy/requirements.txt` запинены целиком: две среды
  и CI обязаны ставить одно и то же.

## Автодеплой

Push в `main` или `dev` → `.github/workflows/ci.yml`:
1. джоб `test` — `compileall` + pytest, на обеих ветках;
2. джоб `deploy` — тот же `deploy/deploy.sh`; **`main` → прод, `dev` → бета**.

Красные тесты **блокируют** деплой. `deploy/deploy.sh` остаётся и для ручного/
аварийного прогона (`TU4KA_ENV=beta ./deploy/deploy.sh`).

Ветка превращается в среду ровно в одном месте — `env.TU4KA_ENV` на джобе
`deploy`. Выражение падает в сторону беты: на прод уезжает только явный `main`.
Список веток при этом ещё и белый (`if:`), иначе `workflow_dispatch` с
произвольного ref уехал бы неизвестно куда.

Concurrency в два слоя: `ci-${{ github.ref }}` по ветке (прогоны на разных
ветках не встают друг за другом) плюс отдельная группа `deploy-tu4ka` на самом
джобе деплоя — прод и бета едут на один VPS и оба трогают nginx, их надо
сериализовать. `cancel-in-progress: false` теперь везде: деплоит и `dev`, так
что рвать идущий прогон нельзя ни на одной ветке.

Smoke-check: прод по **голому IP** (это фактический адрес датчика и
единственная проверка в CI, что его маршрут жив), бета — по
`https://beta.amqi.am/healthz`. Оба с `grep '"ok"'` по телу: голый `curl -fsS`
считает успехом и 301, так что редирект на месте `/healthz` прошёл бы зелёным.

## Ключ деплоя

Отдельный ed25519-ключ в секрете репозитория `TU4KA_SSH_KEY`, добавлен в
`authorized_keys` сервера с префиксом `restrict` (ограниченный shell для CI).
Host key сервера запиннен прямо в workflow-файле — это нормально, он публичный
и виден в git под ревью, приватность тут ни при чём.

## Быстрые проверки

```bash
ssh tu4ka journalctl -u tu4ka -f         # логи прода
ssh tu4ka journalctl -u tu4ka-beta -f    # логи беты
ssh tu4ka systemctl status tu4ka         # состояние сервиса
ssh tu4ka nginx -t                       # конфиг фронта
curl http://178.160.230.131/healthz      # живость по пути датчика (без редиректа)
curl https://amqi.am/healthz             # сайт
curl https://beta.amqi.am/healthz        # бета
gh run list --limit 5                    # прогоны CI
```

## Источники

- Матчасть из [CLAUDE.md](../../CLAUDE.md).
- Workflow: [.github/workflows/ci.yml](../../.github/workflows/ci.yml).
- Скрипты: [deploy/deploy.sh](../../deploy/deploy.sh), [deploy/remote_setup.sh](../../deploy/remote_setup.sh).
