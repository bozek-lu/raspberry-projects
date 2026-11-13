# raspberry-projects

Code and config for **homepi** (Raspberry Pi 4, reachable over Tailscale) plus Mac-side helpers.

| Path | What |
| --- | --- |
| `pi/home/` | Mirror of tracked files in `/home/lbx` on the Pi: Pandora Telegram bot, dashboard helpers, compose files, Homepage config, Home Assistant YAML |
| `pi/home/backup/` | Nightly restic backup to Google Drive (see `restore.md`) |
| `pi/home/server/diun/` | Telegram alerts when container images have updates |
| `pi/home/server/paperless/` | Paperless-ngx for receipts and documents (tailnet only, `:8444`); one-time `setup.sh` |
| `pi/home/receipts.py` | Photos/PDFs sent to Pandora go to Paperless and get linked to the matching Actual transaction |
| `pi/sync-filter.txt` | What gets synced; secrets and data are excluded |
| `claude-usage-push/` | Mac launchd job pushing Claude usage to the Pi |

## Workflow

Edit here, commit, push, then deploy:

```bash
git commit -am "..." && git push
./deploy.sh           # committed + pushed code -> Pi, restarts what changed
./pi-sync.sh pull     # if you edited files directly on the Pi: Pi -> repo, then commit
```

`deploy.sh` refuses to run with uncommitted or unpushed changes, and stops if files on the Pi
were edited since the last deploy (commit hash kept in `~/.deployed_commit` on the Pi).
It restarts Pandora when bot scripts change, runs `docker compose up -d` for changed compose files,
and reloads the backup timer when its units change. It never deletes files on the Pi.

## Secrets

Nothing secret is committed. Real values live only on the Pi:

- `~/.env` - Telegram, Claude, Lotto tokens (bot + scripts read env vars)
- `~/server/*/.env`, `~/server/homepage/secrets.env`, `~/homeassistant/secrets.yaml`
- `/etc/homepi-backup/` - restic password, rclone (Google Drive) config

Guards: `.gitignore`, a gitleaks pre-commit hook (`git config core.hooksPath .githooks`,
needs `brew install gitleaks`) with extra rules in `.gitleaks.toml`, and a gitleaks GitHub Action.
