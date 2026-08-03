# Деплой и CI/CD

## Инфраструктура

- Сервер: VPS `178.160.230.131`, Ubuntu 24.04, 2 ГБ RAM. ssh-алиас `tu4ka`
  (root, ключ `~/.ssh/tu4ka`).
- FastAPI + uvicorn на порту 80, systemd-юнит `tu4ka`;
  `ExecStart=… uvicorn server.main:app --app-dir /opt/tu4ka/app`.
- SQLite `/var/lib/tu4ka/tu4ka.db` (WAL) — **вне** `/opt/tu4ka`, так что деплой
  с `--delete` её не задевает.
- Код на сервере: `/opt/tu4ka/app/server/`, venv `/opt/tu4ka/venv`. Инвариант
  «`app/` содержит ровно `server/`» держит rsync-фильтр в `deploy.sh` —
  см. [server-package-layout](server-package-layout.md).
- Креды push (`TU4KA_PUSH_USER`/`TU4KA_PUSH_PASS`) генерируются один раз в
  `/etc/tu4ka/env` — идемпотентно, `deploy/remote_setup.sh` их не перегенерит
  при повторных запусках.

## Автодеплой

Push в `main` или `dev` → `.github/workflows/ci.yml`:
1. джоб `test` — `compileall` + pytest, на обеих ветках;
2. джоб `deploy` — тот же `deploy/deploy.sh`, но с `TU4KA_HOST=root@178.160.230.131`.

Красные тесты **блокируют** деплой. `deploy/deploy.sh` остаётся и для ручного/
аварийного прогона (`rsync` + `remote_setup.sh`).

`dev` гоняет только тесты: джоб `deploy` отсечён гейтом
`if: github.ref == 'refs/heads/main'`. Тот же гейт защищает от `workflow_dispatch`
с произвольной ветки — без него ручной запуск уехал бы на прод.

Concurrency-группа — `ci-${{ github.ref }}`, то есть по ветке. Одна общая группа
означала бы, что прогоны тестов с `dev` встают в очередь за прод-деплоем;
разводить их безопасно, потому что деплоит только `main`. На `dev`
`cancel-in-progress` включён (интересен последний прогон), на `main` — нет:
рвать идущий деплой нельзя, сервис останется недонакаченным.

## Ключ деплоя

Отдельный ed25519-ключ в секрете репозитория `TU4KA_SSH_KEY`, добавлен в
`authorized_keys` сервера с префиксом `restrict` (ограниченный shell для CI).
Host key сервера запиннен прямо в workflow-файле — это нормально, он публичный
и виден в git под ревью, приватность тут ни при чём.

## Быстрые проверки

```bash
ssh tu4ka journalctl -u tu4ka -f        # логи сервиса
ssh tu4ka systemctl status tu4ka        # состояние сервиса
curl http://178.160.230.131/healthz     # живость
gh run list --limit 5                   # прогоны CI
```

## Источники

- Матчасть из [CLAUDE.md](../../CLAUDE.md).
- Workflow: [.github/workflows/ci.yml](../../.github/workflows/ci.yml).
- Скрипты: [deploy/deploy.sh](../../deploy/deploy.sh), [deploy/remote_setup.sh](../../deploy/remote_setup.sh).
