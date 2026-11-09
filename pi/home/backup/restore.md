# Restoring homepi from backup

Needs: the restic password (stored off the Pi) and a Google Drive rclone remote named `gdrive`.

```bash
sudo apt install restic rclone sqlite3
rclone config            # create remote "gdrive" (Google Drive, scope drive.file) - or copy rclone.conf
export RESTIC_REPOSITORY=rclone:gdrive:homepi-backup
export RESTIC_PASSWORD_FILE=/path/to/restic-password
restic snapshots
restic restore latest --target /tmp/restore
```

- Files land under `/tmp/restore/home/lbx/...`.
- Databases: `/tmp/restore/var/backups/homepi-stage/db/` holds consistent copies; put them back as
  `server/vaultwarden/data/db.sqlite3`, `server/uptime-kuma/data/kuma.db`,
  `homeassistant/home-assistant_v2.db` (with the containers stopped).
- Cron: `crontab-lbx.txt`; bot service: `telebot.service`; containers list and packages are alongside.
- Single file: `restic restore latest --target /tmp/r --include /home/lbx/morning.py`.
