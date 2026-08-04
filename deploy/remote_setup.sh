#!/usr/bin/env bash
# Идемпотентная настройка сервера. Запускается deploy.sh после rsync файлов
# в $ROOT/{app,deploy}, где $ROOT зависит от среды.
#
# Среда — TU4KA_ENV: prod или beta. Прод и бета живут на одном VPS, разведены
# по путям, юнитам и портам; наружу их разводит nginx по server_name.
set -euo pipefail

ENV=${TU4KA_ENV:-prod}
case "$ENV" in
    prod) NAME=tu4ka;      PORT=8000; CERT_NAME=amqi.am;      CERT_ARGS="-d amqi.am -d www.amqi.am"; TLS_CONF=tu4ka-tls.conf ;;
    beta) NAME=tu4ka-beta; PORT=8001; CERT_NAME=beta.amqi.am; CERT_ARGS="-d beta.amqi.am";           TLS_CONF=tu4ka-beta.conf ;;
    *) echo "неизвестный TU4KA_ENV: $ENV" >&2; exit 1 ;;
esac

ROOT=/opt/$NAME
ETC=/etc/$NAME
DB_DIR=/var/lib/$NAME
ACME_EMAIL=krotghar@gmail.com

# Страховка от главного сценария порчи: копия этого скрипта лежит и в
# /opt/tu4ka-beta/deploy/. Запущенная там без TU4KA_ENV, она по умолчанию
# считала бы себя продом и перезаписала бы прод-юнит бета-путями.
SELF_ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ "$SELF_ROOT" = "$ROOT" ] || {
    echo "TU4KA_ENV=$ENV (ожидается корень $ROOT), но скрипт лежит в $SELF_ROOT — отказ" >&2
    exit 1
}

echo "== настройка среды $ENV ($NAME, порт $PORT)"

# --- пользователь и каталоги ------------------------------------------------
# Юзер один на обе среды: изоляция идёт по путям и ReadWritePaths в юнитах.
id -u tu4ka >/dev/null 2>&1 || useradd --system --home /var/lib/tu4ka --shell /usr/sbin/nologin tu4ka

mkdir -p "$DB_DIR" "$ETC"
chown tu4ka:tu4ka "$DB_DIR"

# --- пакеты -----------------------------------------------------------------
need_pkg=""
for p in python3-venv nginx certbot; do
    dpkg -s "$p" >/dev/null 2>&1 || need_pkg="$need_pkg $p"
done
if [ -n "$need_pkg" ]; then
    apt-get update -qq
    # postinst пакета nginx стартует сервис, а :80 в момент переезда ещё держит
    # uvicorn: старт упал бы и уронил dpkg. policy-rc.d ровно для этого.
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod +x /usr/sbin/policy-rc.d
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need_pkg
    rm -f /usr/sbin/policy-rc.d
fi

# --- nginx: общий фронт -----------------------------------------------------
mkdir -p /var/www/acme/.well-known/acme-challenge
chmod -R 755 /var/www/acme

if [ "$ENV" = prod ]; then
    # Дефолтный сайт Debian — тоже default_server на :80, был бы конфликт.
    rm -f /etc/nginx/sites-enabled/default
    cp "$ROOT/deploy/nginx/tu4ka.conf" /etc/nginx/sites-available/tu4ka.conf
    ln -sfn /etc/nginx/sites-available/tu4ka.conf /etc/nginx/sites-enabled/tu4ka.conf
    install -m 755 -D "$ROOT/deploy/nginx/reload-nginx" /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
    ufw allow 443/tcp >/dev/null 2>&1 || true
else
    # Общий фронт с default_server на :80 ставит только прод. Без него :80
    # достался бы бета-блоку, и датчик, ходящий на голый IP, улетел бы
    # в редирект на https — с потерей Authorization.
    [ -e /etc/nginx/sites-enabled/tu4ka.conf ] || {
        echo "нет /etc/nginx/sites-enabled/tu4ka.conf — сначала выкатите прод" >&2
        exit 1
    }
fi

# Проверка конфига ничего не биндит и проходит, пока :80 ещё у uvicorn.
# Если она падает — выходим до правки юнитов, прод остаётся нетронутым.
nginx -t

# --- venv -------------------------------------------------------------------
[ -d "$ROOT/venv" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --upgrade pip
"$ROOT/venv/bin/pip" install -q -r "$ROOT/deploy/requirements.txt"

# --- креды push -------------------------------------------------------------
# Генерируются один раз: прод-пароль прошит в датчике, перегенерация оторвала бы
# приём. У беты свой независимый пароль.
if [ ! -f "$ETC/env" ]; then
    PUSH_PASS=$(openssl rand -hex 16)
    printf 'TU4KA_DB=%s/tu4ka.db\nTU4KA_PUSH_USER=tu4ka\nTU4KA_PUSH_PASS=%s\n' "$DB_DIR" "$PUSH_PASS" > "$ETC/env"
    chmod 600 "$ETC/env"
fi

# --- юниты ------------------------------------------------------------------
# Переезд с :80 за nginx случается ровно один раз. Запоминаем факт до
# перезаписи юнита: только на этом прогоне уместен автооткат.
CUTOVER=0
if [ "$ENV" = prod ] && grep -q -- '--port 80' /etc/systemd/system/$NAME.service 2>/dev/null; then
    CUTOVER=1
    cp /etc/systemd/system/$NAME.service /etc/systemd/system/$NAME.service.prev
    echo "== переезд с :80 за nginx; прежний юнит сохранён в $NAME.service.prev"
fi

cp "$ROOT/deploy/$NAME.service" /etc/systemd/system/$NAME.service
cp "$ROOT/deploy/$NAME.socket"  /etc/systemd/system/$NAME.socket
systemctl daemon-reload
systemctl enable "$NAME.socket" >/dev/null 2>&1
systemctl enable "$NAME" >/dev/null 2>&1
# Сокет поднимаем один раз и дальше не трогаем: слушающий дескриптор держит
# systemd, поэтому рестарт .service не закрывает порт — соединения ждут
# в очереди ядра. Рестарт .socket вернул бы дырку.
systemctl start "$NAME.socket"

# --- бета: пересев БД из прода ---------------------------------------------
if [ "$ENV" = beta ]; then
    echo "== пересев БД из прода"
    systemctl stop "$NAME" 2>/dev/null || true
    rm -f "$DB_DIR/tu4ka.db.new"
    # VACUUM INTO, а не cp: база в WAL, и плоская копия без -wal невалидна.
    # mode=ro обязателен — root, открывший прод-базу на запись, создал бы
    # -wal/-shm от root, и прод-сервис (юзер tu4ka) перестал бы писать.
    "$ROOT/venv/bin/python" - /var/lib/tu4ka/tu4ka.db "$DB_DIR/tu4ka.db.new" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
con.execute("VACUUM INTO ?", (dst,))
con.close()
PY
    # Старые -wal/-shm снимаем только после остановки сервиса: удалять их
    # из-под живого соединения — верный способ получить битую базу.
    rm -f "$DB_DIR/tu4ka.db-wal" "$DB_DIR/tu4ka.db-shm"
    mv -f "$DB_DIR/tu4ka.db.new" "$DB_DIR/tu4ka.db"
    chown tu4ka:tu4ka "$DB_DIR/tu4ka.db"
    chmod 640 "$DB_DIR/tu4ka.db"
fi

systemctl restart "$NAME"

# --- nginx: старт/перезагрузка ---------------------------------------------
systemctl enable nginx >/dev/null 2>&1
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
else
    systemctl start nginx
fi

# --- проверка живости -------------------------------------------------------
# grep по телу, а не голый `curl -fsS`: последний считает успехом и 301,
# так что редирект на месте /healthz прошёл бы как зелёный.
wait_healthz() {
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if curl -fsS -m 5 "$1/healthz" 2>/dev/null | grep -q '"ok"'; then
            return 0
        fi
        sleep 2
    done
    return 1
}

ok=1
wait_healthz "http://127.0.0.1:$PORT" || { ok=0; echo "приложение не ответило на /healthz" >&2; }
# Прод проверяем ещё и сквозь nginx: приложение может быть живым, а фронт —
# сломанным, и для датчика это одно и то же.
if [ "$ok" = 1 ] && [ "$ENV" = prod ]; then
    wait_healthz "http://127.0.0.1" || { ok=0; echo "nginx не проксирует /healthz" >&2; }
fi

if [ "$ok" != 1 ]; then
    if [ "$CUTOVER" = 1 ]; then
        # Гейт по CUTOVER обязателен: без него обычный деплой с битым кодом
        # тоже останавливал бы nginx и выставлял сломанное приложение на :80.
        echo "откат переезда: uvicorn обратно на :80, nginx остановлен" >&2
        systemctl stop nginx || true
        systemctl stop "$NAME" "$NAME.socket" || true
        cp /etc/systemd/system/$NAME.service.prev /etc/systemd/system/$NAME.service
        systemctl daemon-reload
        systemctl restart "$NAME"
    fi
    exit 1
fi

# --- TLS --------------------------------------------------------------------
# HTTP-01 через webroot, не через плагин --nginx: деплой перезаписывает конфиг
# на каждом прогоне, и правки плагина были бы молча затёрты.
# Гейт по каталогу lineage — единственная защита от rate limit LE: certbot
# запускается ровно один раз на имя за всю жизнь сервера.
if [ ! -d "/etc/letsencrypt/live/$CERT_NAME" ]; then
    certbot certonly --webroot -w /var/www/acme \
        --cert-name "$CERT_NAME" $CERT_ARGS \
        --non-interactive --agree-tos -m "$ACME_EMAIL" --keep-until-expiring \
        || echo "certbot: выпуск для $CERT_NAME не удался, TLS-конфиг не ставим" >&2
fi

if [ -d "/etc/letsencrypt/live/$CERT_NAME" ]; then
    cp "$ROOT/deploy/nginx/$TLS_CONF" "/etc/nginx/sites-available/$TLS_CONF"
    ln -sfn "/etc/nginx/sites-available/$TLS_CONF" "/etc/nginx/sites-enabled/$TLS_CONF"
    if nginx -t; then
        systemctl reload nginx
    else
        # Снимаем только что добавленный TLS-блок: HTTP-фронт должен пережить
        # любую ошибку в нём, иначе отвалится приём от датчика.
        rm -f "/etc/nginx/sites-enabled/$TLS_CONF"
        nginx -t && systemctl reload nginx
        echo "TLS-конфиг $TLS_CONF не прошёл nginx -t и снят" >&2
        exit 1
    fi
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
fi

sleep 1
systemctl --no-pager --lines=5 status "$NAME"
