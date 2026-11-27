#!/bin/bash
# Deploy the committed, pushed state of pi/home to the Pi and restart what changed.
#   ./deploy.sh           normal deploy
#   ./deploy.sh --force   overwrite files that were edited directly on the Pi
set -euo pipefail
cd "$(dirname "$0")"
PI=lbx@homepi.tail04e168.ts.net
RSYNC=$(command -v /opt/homebrew/bin/rsync || command -v rsync)
FILTER=(--filter="merge pi/sync-filter.txt")
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

# 1. Only code that is committed and on GitHub goes to the Pi
if [ -n "$(git status --porcelain)" ]; then
	echo "Uncommitted changes - commit them first:"; git status --short; exit 1
fi
git fetch -q origin
if [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]; then
	echo "Local main and GitHub differ - git push (or pull) first."; exit 1
fi
HEAD=$(git rev-parse HEAD)

# 2. Refuse to overwrite edits made directly on the Pi since the last deploy
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
DEPLOYED=$(ssh -o BatchMode=yes "$PI" 'cat ~/.deployed_commit 2>/dev/null' || true)
if [ "$FORCE" = 0 ]; then
	if [ -z "$DEPLOYED" ]; then
		echo "The Pi has no ~/.deployed_commit, so edits made there can't be detected. Use --force once."; exit 1
	fi
	mkdir -p "$TMP/pi" "$TMP/git"
	"$RSYNC" -a --prune-empty-dirs "${FILTER[@]}" "$PI:/home/lbx/" "$TMP/pi/"
	git archive "$DEPLOYED" pi/home | tar -x -C "$TMP/git"
	if ! diff -rq "$TMP/git/pi/home" "$TMP/pi" > "$TMP/drift"; then
		echo "Files on the Pi changed since the last deploy (${DEPLOYED:0:7}):"
		sed "s|$TMP/git/pi/home|repo|; s|$TMP/pi|pi|; s/^/  /" "$TMP/drift"
		echo "Keep them: ./pi-sync.sh pull, commit, push, deploy again. Drop them: ./deploy.sh --force"
		exit 1
	fi
fi

# 3. Copy changed files (never deletes anything on the Pi)
"$RSYNC" -a --checksum --out-format='%i %n' "${FILTER[@]}" pi/home/ "$PI:/home/lbx/" \
	| awk '$1 ~ /^<f/ {print $2}' > "$TMP/changed"
if [ ! -s "$TMP/changed" ]; then
	echo "Nothing changed on the Pi."
else
	echo "Updated on the Pi:"; sed 's/^/  /' "$TMP/changed"
fi

# 4. Restart only what the change affects
RESTART_BOT=0; RELOAD_UNITS=0; COMPOSE_DIRS=(); HA_CONFIG=0; RESTART_TUTOR=0
while read -r f; do
	case "$f" in
		*/compose.yaml) COMPOSE_DIRS+=("$(dirname "$f")") ;;
		backup/homepi-backup.service|backup/homepi-backup.timer) RELOAD_UNITS=1 ;;
		homeassistant/*.yaml) HA_CONFIG=1 ;;
		server/piano/tutor/server.py|server/piano/tutor/piano-tutor.service) RESTART_TUTOR=1 ;;
		*/*) ;;
		*.py) RESTART_BOT=1 ;;
	esac
done < "$TMP/changed"

if [ "$RESTART_BOT" = 1 ]; then
	echo "Restarting Pandora (telebot)..."
	ssh "$PI" 'sudo systemctl restart telebot && sleep 3 && systemctl is-active telebot'
fi
for dir in ${COMPOSE_DIRS[@]+"${COMPOSE_DIRS[@]}"}; do
	echo "Applying $dir/compose.yaml..."
	ssh "$PI" "cd ~/$dir && docker compose up -d"
done
if [ "$RELOAD_UNITS" = 1 ]; then
	echo "Reloading backup timer..."
	ssh "$PI" 'sudo install -m 644 ~/backup/homepi-backup.service ~/backup/homepi-backup.timer /etc/systemd/system/ && sudo systemctl daemon-reload'
fi
if [ "$RESTART_TUTOR" = 1 ]; then
	echo "Restarting the piano tutor..."
	ssh "$PI" 'sudo install -m 644 ~/server/piano/tutor/piano-tutor.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now piano-tutor && sudo systemctl restart piano-tutor'
fi
[ "$HA_CONFIG" = 1 ] && echo "Home Assistant YAML changed: reload it in HA (Developer tools > YAML) or restart the container."

ssh "$PI" "echo $HEAD > ~/.deployed_commit"
echo "Deployed ${HEAD:0:7}."
