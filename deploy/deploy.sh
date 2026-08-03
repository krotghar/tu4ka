#!/usr/bin/env bash
# Деплой на сервер: rsync кода + запуск remote_setup.sh.
# Хост задаётся ssh-алиасом tu4ka (~/.ssh/config) или TU4KA_HOST.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${TU4KA_HOST:-tu4ka}

ssh "$HOST" 'command -v rsync >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync); mkdir -p /opt/tu4ka/app /opt/tu4ka/deploy'
# server/ — пакет, и на сервере он должен лежать как /opt/tu4ka/app/server/,
# чтобы работал `uvicorn server.main:app`. Передаём корень репозитория с фильтром
# «только server/», а не сам server/: тогда --delete работает на уровне app/, и
# --delete-excluded вычищает всё, чего в server/ нет — включая плоскую раскладку
# (main.py, aqi.py, static/ прямо в app/), оставшуюся от версии до пакетирования,
# и серверные __pycache__. Инвариант: app/ содержит ровно server/.
# Порядок правил важен — rsync применяет первое совпавшее.
rsync -az --delete --delete-excluded \
      --exclude '__pycache__' --include '/server/' --include '/server/**' --exclude '*' \
      ./ "$HOST":/opt/tu4ka/app/
rsync -az --delete deploy/ "$HOST":/opt/tu4ka/deploy/
ssh "$HOST" 'bash /opt/tu4ka/deploy/remote_setup.sh'
