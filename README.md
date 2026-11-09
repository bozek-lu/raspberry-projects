# raspberry-projects

Code and config for **homepi** (Raspberry Pi 4, reachable over Tailscale) plus Mac-side helpers.

| Path | What |
| --- | --- |
| `pi/home/` | Mirror of tracked files in `/home/lbx` on the Pi: Pandora Telegram bot, dashboard helpers, compose files, Homepage config, Home Assistant YAML |
| `pi/home/backup/` | Nightly restic backup to Google Drive (see `restore.md`) |
| `pi/home/server/diun/` | Telegram alerts when container images have updates |
| `pi/sync-filter.txt` | What gets synced; secrets and data are excluded |
| `claude-usage-push/` | Mac launchd job pushing Claude usage to the Pi |

## Workflow

```bash
./pi-sync.sh pull     # Pi -> repo, then: git diff
./pi-sync.sh push     # repo -> Pi (never deletes)
```

## Secrets

Nothing secret is committed. Real values live only on the Pi:

- `~/.env` - Telegram, Claude, Lotto tokens (bot + scripts read env vars)
- `~/server/*/.env`, `~/server/homepage/secrets.env`, `~/homeassistant/secrets.yaml`
- `/etc/homepi-backup/` - restic password, rclone (Google Drive) config

Guards: `.gitignore`, a gitleaks pre-commit hook (`git config core.hooksPath .githooks`,
needs `brew install gitleaks`) with extra rules in `.gitleaks.toml`, and a gitleaks GitHub Action.
