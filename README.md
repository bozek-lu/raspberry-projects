# raspberry-projects

Code and config for **homepi** (Raspberry Pi 4, reachable over Tailscale) plus Mac-side helpers.

| Path | What |
| --- | --- |
| `pi/home/` | Mirror of tracked files in `/home/lbx` on the Pi: Pandora Telegram bot, dashboard helpers, compose files, Homepage config, Home Assistant YAML |
| `pi/home/backup/` | Nightly restic backup to Google Drive (see `restore.md`) |
| `pi/home/server/diun/` | Telegram alerts when container images have updates |
| `pi/home/server/paperless/` | Paperless-ngx for receipts and documents (tailnet only, `:8444`); one-time `setup.sh` |
| `pi/home/receipts.py` | Photos/PDFs sent to Pandora go to Paperless and get linked to the matching Actual transaction |
| `pi/home/piano.py`, `pi/home/server/piano/` | Piano songs for the EZ-310 tutor ([piano-tutor](https://github.com/bozek-lu/piano-tutor)), see below; song server on `:8095` for the Tab5 |
| `tools/piano-convert.py` | Converts the Pi's piano queue on a faster computer |
| `pi/sync-filter.txt` | What gets synced; secrets and data are excluded |
| `claude-usage-push/` | Mac launchd job pushing Claude usage to the Pi |

## Piano songs (Pandora)

- **YouTube link or audio file** -> Pandora downloads it into the queue (`~/server/piano/queue`, 16 kHz WAV)
  and asks how to write down the notes:
  - ⚡ fast here: Spotify basic-pitch, about the song's length on the Pi 4, rough (about 60% note agreement)
  - 🎯 accurate here: ByteDance piano transcription, about 20x the song's length on the Pi 4
  - 💻 on a computer: leave it queued and run `python tools/piano-convert.py` on a Mac/PC (same accurate
    model, much faster; sets up its own Python environment on first run; needs ssh to the Pi)
- **MIDI file** -> straight into the songs.
- Songs get the right hand on channel 1 and the left on channel 2 (EZ-310 light channels), notes folded
  into the keyboard's 61 keys. Pandora sends each finished song; the Tab5 reads `http://homepi.local:8095/songs.json`.
- `/piano` lists songs, `/piano kolejka` the queue (with convert / delete buttons).

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
- `~/server/actual/bank-sync/organize.local.json` - names used by `organize.mjs` (git-ignored)
- `/etc/homepi-backup/` - restic password, rclone (Google Drive) config

Guards: `.gitignore`, a gitleaks pre-commit hook (`git config core.hooksPath .githooks`,
needs `brew install gitleaks`) with extra rules in `.gitleaks.toml`, and a gitleaks GitHub Action.
