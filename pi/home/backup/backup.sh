#!/bin/bash
# Nightly encrypted backup of homepi to Google Drive (restic over rclone).
# Runs as root from homepi-backup.timer. Secrets live only in /etc/homepi-backup/.
set -Eeuo pipefail

CONF=/etc/homepi-backup
HERE=$(dirname "$(readlink -f "$0")")
STAGE=/var/backups/homepi-stage
STATUS=/home/lbx/backup_status.json
export RESTIC_REPOSITORY=rclone:gdrive:homepi-backup
export RESTIC_PASSWORD_FILE=$CONF/restic-password
export RCLONE_CONFIG=$CONF/rclone.conf
export RESTIC_CACHE_DIR=/var/cache/restic

# TELEGRAM_API_TOKEN / TELEGRAM_OWNER_ID come from the bot's env file
set -a; . /home/lbx/.env; set +a
KUMA_BACKUP_PUSH_URL=""
[ -f "$CONF/backup.env" ] && . "$CONF/backup.env"

telegram() {
	curl -fsS -m 20 "https://api.telegram.org/bot${TELEGRAM_API_TOKEN}/sendMessage" \
		--data-urlencode "chat_id=${TELEGRAM_OWNER_ID}" --data-urlencode "text=$1" >/dev/null || true
}
write_status() {
	printf '{"ok": %s, "time": "%s", "message": "%s"}\n' "$1" "$(date '+%d.%m %H:%M')" "$2" > "$STATUS.tmp"
	chown lbx:lbx "$STATUS.tmp"; mv "$STATUS.tmp" "$STATUS"
}
on_error() {
	write_status false "failed at line $1"
	telegram "❌ Backup homepi nie wyszedł (linia $1). Sprawdź: journalctl -u homepi-backup"
	[ -n "$KUMA_BACKUP_PUSH_URL" ] && curl -fsS -m 10 "${KUMA_BACKUP_PUSH_URL}?status=down&msg=failed" >/dev/null || true
}
trap 'on_error $LINENO' ERR

# 1. Consistent copies of live SQLite databases (safe while the apps run)
rm -rf "$STAGE"; mkdir -p "$STAGE/db"; chmod 700 "$STAGE"
snap() { [ -f "$1" ] && sqlite3 "$1" ".backup '$STAGE/db/$2'"; }
snap /home/lbx/server/vaultwarden/data/db.sqlite3 vaultwarden.sqlite3
snap /home/lbx/server/uptime-kuma/data/kuma.db uptime-kuma.db
snap /home/lbx/homeassistant/home-assistant_v2.db home-assistant_v2.db

# 2. System bits that live outside /home
crontab -l -u lbx > "$STAGE/crontab-lbx.txt" 2>/dev/null || true
cp /etc/systemd/system/telebot.service "$STAGE/" 2>/dev/null || true
docker ps -a --format '{{.Names}}\t{{.Image}}' > "$STAGE/containers.txt"
dpkg --get-selections > "$STAGE/packages.txt"

# 3. Back up, thin out old snapshots, verify part of the data once a week
restic backup --quiet --tag nightly --exclude-file="$HERE/excludes.txt" /home/lbx "$STAGE"
restic forget --quiet --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
if [ "$(date +%u)" = 7 ]; then restic check --quiet --read-data-subset=10%; fi

SIZE=$(restic stats latest --json | python3 -c 'import json,sys; print(round(json.load(sys.stdin)["total_size"]/1e6))')
write_status true "${SIZE} MB"
[ -n "$KUMA_BACKUP_PUSH_URL" ] && curl -fsS -m 10 "${KUMA_BACKUP_PUSH_URL}?status=up&msg=${SIZE}MB" >/dev/null || true
echo "backup ok, ${SIZE} MB"
