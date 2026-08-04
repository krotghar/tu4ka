#!/usr/bin/env bash
# Ежедневный бэкап боевой базы. Запускается таймером tu4ka-backup.timer,
# кредами и путями снабжается через EnvironmentFile=/etc/tu4ka/env.
#
# ТОЛЬКО ПРОД. База беты — одноразовый снимок, пересеваемый на каждом её
# деплое; бэкапить её нечего, и remote_setup.sh ставит эти юниты только
# в прод-ветке.
set -euo pipefail

DB=${TU4KA_DB:-/var/lib/tu4ka/tu4ka.db}
DIR=${TU4KA_BACKUP_DIR:-/var/backups/tu4ka}
PY=${TU4KA_PYTHON:-/opt/tu4ka/venv/bin/python}
KEEP_DAILY=7
KEEP_MONTHLY=6

DAY=$(date -u +%Y%m%d)
MONTH=$(date -u +%Y%m)
mkdir -p "$DIR/daily" "$DIR/monthly"

raw="$DIR/tu4ka-$DAY.db"
out="$DIR/daily/tu4ka-$DAY.db.zst"
rm -f "$raw"

# --- снимок ------------------------------------------------------------------
# VACUUM INTO, а не cp: база в WAL, и плоская копия без -wal невалидна.
# mode=ro обязателен — тот же инвариант, что в пересеве беты: процесс,
# открывший боевую базу на запись, создал бы -wal/-shm от своего юзера.
#
# integrity_check прогоняется по САМОМУ ФАЙЛУ БЭКАПА, а не по источнику:
# проверять надо то, что потом будут восстанавливать. Бэкап, который не
# открывается, хуже отсутствующего — он создаёт ложное спокойствие.
"$PY" - "$DB" "$raw" <<'PY'
import sqlite3, sys

src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
con.execute("VACUUM INTO ?", (dst,))
con.close()

chk = sqlite3.connect(dst)
verdict = chk.execute("PRAGMA integrity_check").fetchone()[0]
rows = chk.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
chk.close()
if verdict != "ok":
    sys.exit(f"integrity_check провалился: {verdict}")
print(f"снимок: {rows} строк, integrity_check ok")
PY

zstd -12 -q -f -o "$out" "$raw"
rm -f "$raw"
echo "сжато: $(du -h "$out" | cut -f1)"

# Первое число месяца дублируем в monthly/ — так недельная глубина ежедневных
# не съедает длинную ретроспективу.
if [ "$(date -u +%d)" = 01 ]; then
    cp -f "$out" "$DIR/monthly/tu4ka-$MONTH.db.zst"
fi

# --- ротация -----------------------------------------------------------------
# Имена генерируем сами (только цифры и дефисы), так что ls в пайпе безопасен.
prune() {
    local d=$1 keep=$2
    ls -1t "$d" 2>/dev/null | tail -n +$((keep + 1)) | while read -r f; do
        echo "ротация: удаляю $d/$f"
        rm -f "$d/$f"
    done
}
prune "$DIR/daily" "$KEEP_DAILY"
prune "$DIR/monthly" "$KEEP_MONTHLY"

# Печатаем суммарный размер: диск VPS 10 ГБ, база растёт ~430 МБ/год
# несжатыми, и увидеть это надо до того, как место кончится.
echo "каталог бэкапов: $(du -sh "$DIR" | cut -f1), свободно на диске: $(df -h "$DIR" | awk 'NR==2 {print $4}')"

# --- офсайт ------------------------------------------------------------------
# Локальная копия уже лежит и валидна, поэтому провал загрузки не отменяет
# успешный бэкап — но код возврата ненулевой, чтобы это было видно в журнале
# и в статусе юнита.
if [ -z "${TU4KA_S3_ENDPOINT:-}" ] || [ -z "${TU4KA_S3_BUCKET:-}" ] || [ -z "${TU4KA_S3_KEY:-}" ]; then
    echo "офсайт не настроен (TU4KA_S3_* пустые) — только локальная копия"
    exit 0
fi

obj="tu4ka/$(date -u +%Y/%m)/tu4ka-$DAY.db.zst"
# --aws-sigv4 есть в curl с 7.75; на сервере 8.5. Отдельный клиент
# (awscli/rclone) ради одной загрузки в день ставить незачем.
# --fail-with-body, а не --fail: иначе XML с причиной отказа теряется.
if curl --fail-with-body -sS --aws-sigv4 "aws:amz:${TU4KA_S3_REGION:-auto}:s3" \
        --user "$TU4KA_S3_KEY:$TU4KA_S3_SECRET" \
        -T "$out" "${TU4KA_S3_ENDPOINT%/}/${TU4KA_S3_BUCKET}/$obj"; then
    echo "офсайт: загружено $obj"
else
    echo "офсайт: загрузка $obj НЕ УДАЛАСЬ; локальная копия на месте" >&2
    exit 1
fi

# Удалением в бэкап-хранилище скрипт не занимается сознательно: срок жизни
# объектов задаётся lifecycle-правилом провайдера. Код, умеющий стирать
# бэкапы, — лишнее оружие в руках возможного бага.
