#!/usr/bin/env bash
# Деплой на сервер: rsync кода + запуск remote_setup.sh.
# Хост задаётся ssh-алиасом tu4ka (~/.ssh/config) или TU4KA_HOST.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${TU4KA_HOST:-tu4ka}

ssh "$HOST" 'command -v rsync >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync); mkdir -p /opt/tu4ka/app /opt/tu4ka/deploy'
rsync -az --delete server/ "$HOST":/opt/tu4ka/app/
rsync -az --delete deploy/ "$HOST":/opt/tu4ka/deploy/
ssh "$HOST" 'bash /opt/tu4ka/deploy/remote_setup.sh'
