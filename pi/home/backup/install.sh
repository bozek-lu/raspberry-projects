#!/bin/bash
# One-time setup (run with sudo): config folder, restic password, systemd timer, repo init.
set -euo pipefail
CONF=/etc/homepi-backup
HERE=$(dirname "$(readlink -f "$0")")
install -d -m 700 "$CONF" /var/cache/restic
if [ ! -f "$CONF/restic-password" ]; then
	(umask 077; openssl rand -base64 32 > "$CONF/restic-password")
	echo "Generated $CONF/restic-password - copy it somewhere OFF the Pi now."
fi
[ -f "$CONF/rclone.conf" ] || { echo "Missing $CONF/rclone.conf (Google Drive remote 'gdrive')"; exit 1; }
chmod 600 "$CONF"/*
install -m 644 "$HERE/homepi-backup.service" "$HERE/homepi-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now homepi-backup.timer
export RESTIC_REPOSITORY=rclone:gdrive:homepi-backup RESTIC_PASSWORD_FILE=$CONF/restic-password RCLONE_CONFIG=$CONF/rclone.conf
restic cat config >/dev/null 2>&1 || restic init
echo "Installed. Next run: $(systemctl show homepi-backup.timer -p NextElapseUSecRealtime --value)"
