#!/usr/bin/env bash
# Деплой на сервер: rsync кода + запуск remote_setup.sh.
# Хост задаётся ssh-алиасом tu4ka (~/.ssh/config) или TU4KA_HOST.
#
# Среда — TU4KA_ENV: prod (по умолчанию) или beta. Обе живут на одном VPS и
# разведены целиком по путям и именам юнитов; наружу их разводит nginx по
# server_name. См. deploy/nginx/ и wiki/pages/nginx-tls-beta.md.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${TU4KA_HOST:-tu4ka}
ENV=${TU4KA_ENV:-prod}

case "$ENV" in
    prod) NAME=tu4ka ;;
    beta) NAME=tu4ka-beta ;;
    *) echo "TU4KA_ENV должен быть prod или beta, получено: $ENV" >&2; exit 1 ;;
esac
ROOT=/opt/$NAME

echo "деплой: среда=$ENV, корень=$ROOT, хост=$HOST"

ssh "$HOST" "command -v rsync >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync); mkdir -p $ROOT/app $ROOT/deploy"
# server/ — пакет, и на сервере он должен лежать как $ROOT/app/server/,
# чтобы работал `uvicorn server.main:app`. Передаём корень репозитория с фильтром
# «только server/», а не сам server/: тогда --delete работает на уровне app/, и
# --delete-excluded вычищает всё, чего в server/ нет — включая плоскую раскладку
# (main.py, aqi.py, static/ прямо в app/), оставшуюся от версии до пакетирования,
# и серверные __pycache__. Инвариант: app/ содержит ровно server/.
# Порядок правил важен — rsync применяет первое совпавшее.
rsync -az --delete --delete-excluded \
      --exclude '__pycache__' --include '/server/' --include '/server/**' --exclude '*' \
      ./ "$HOST":"$ROOT/app/"
rsync -az --delete deploy/ "$HOST":"$ROOT/deploy/"
# ssh не пробрасывает переменные окружения (SendEnv/AcceptEnv требуют правки
# sshd), поэтому передаём префиксом команды.
ssh "$HOST" "TU4KA_ENV=$ENV bash $ROOT/deploy/remote_setup.sh"
