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
| `pi/home/server/piano/tutor/` | Piano tutor for the iPad: page, practice stats, song library (`:8096`, tailnet `:8445`), see below |
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

## Piano tutor (tablet)

`server/piano/tutor/`: `server.py` (Python standard library, systemd unit `piano-tutor.service`, port 8096)
serves `index.html`, the songs and a small API. Addresses: `http://homepi.local:8096` (LAN) and
`https://homepi.tail04e168.ts.net:8445` (tailnet, `tailscale serve --bg --https=8445 http://127.0.0.1:8096`).
The EZ-310 plugs into the **tablet** (USB-B to USB-C/Lightning cable or Apple camera adapter); the page does the
tutoring in the browser with Web MIDI: lights the next notes on the light channels (1 = right, 2 = left), reads
key presses, waits for the right keys ("Wait for me") or plays in time at a chosen tempo ("Play along").

- iPad Safari has no Web MIDI: open the page in the free **Web MIDI Browser** app. Chrome/Edge on a computer or
  Android need the https address.
- Options (⚙): Guide sound (lit notes muted with CC7 = 0 on channels 1/2 otherwise), Other hand (the hand you
  are not practising, on channel 3), Count-in (4 GM wood-block clicks on channel 10), Speed up (+5% tempo after a
  Play along run with 90% right, up to 100%). A / B on the bottom bar repeat a part of the song.
- ☰ Songs: practice minutes (today, week, streak, last 4 weeks), add a YouTube link or upload a MIDI/audio file
  (same code as Pandora: `~/piano.py` and the transcriber), the conversion queue with ⚡/🎯 buttons, rename/delete.
- Practice sessions go to `~/server/piano/practice.json` (time counts while playing; in Wait for me only with a
  key press in the last 20 s). The Homepage tile (top "Raspberry Pi" section) reads `/api/stats`.
- On the keyboard: Function 041 Light Guide On, 042/043 light channels 1/2 (defaults).
- `deploy.sh` restarts the service when `server.py` or the unit changes; `index.html` needs only a page reload.

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
