#!/usr/bin/env bash
# Идемпотентная настройка сервера. Запускается deploy.sh после rsync
# файлов в /opt/tu4ka/{app,deploy}.
set -euo pipefail

id -u tu4ka >/dev/null 2>&1 || useradd --system --home /var/lib/tu4ka --shell /usr/sbin/nologin tu4ka

mkdir -p /var/lib/tu4ka /etc/tu4ka
chown tu4ka:tu4ka /var/lib/tu4ka

if ! dpkg -s python3-venv >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
fi

[ -d /opt/tu4ka/venv ] || python3 -m venv /opt/tu4ka/venv
/opt/tu4ka/venv/bin/pip install -q --upgrade pip
/opt/tu4ka/venv/bin/pip install -q -r /opt/tu4ka/deploy/requirements.txt

# Учётные данные для push от датчика — генерируются один раз
if [ ! -f /etc/tu4ka/env ]; then
    PUSH_PASS=$(openssl rand -hex 16)
    printf 'TU4KA_DB=/var/lib/tu4ka/tu4ka.db\nTU4KA_PUSH_USER=tu4ka\nTU4KA_PUSH_PASS=%s\n' "$PUSH_PASS" > /etc/tu4ka/env
    chmod 600 /etc/tu4ka/env
fi

cp /opt/tu4ka/deploy/tu4ka.service /etc/systemd/system/tu4ka.service
systemctl daemon-reload
systemctl enable tu4ka >/dev/null 2>&1
systemctl restart tu4ka
sleep 2
systemctl --no-pager --lines=5 status tu4ka
