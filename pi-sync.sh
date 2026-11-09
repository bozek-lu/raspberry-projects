#!/bin/sh
# Sync code between this repo (pi/home) and the Pi's home folder.
#   ./pi-sync.sh pull   Pi -> repo (then review with git diff)
#   ./pi-sync.sh push   repo -> Pi (never deletes; secrets and data are filtered out)
set -eu
cd "$(dirname "$0")"
PI=lbx@homepi.tail04e168.ts.net
RSYNC=$(command -v /opt/homebrew/bin/rsync || command -v rsync)
case "${1:-}" in
	pull) "$RSYNC" -a --prune-empty-dirs --filter="merge pi/sync-filter.txt" "$PI:/home/lbx/" pi/home/ ;;
	push) "$RSYNC" -av --filter="merge pi/sync-filter.txt" pi/home/ "$PI:/home/lbx/" ;;
	*) echo "usage: $0 pull|push" >&2; exit 1 ;;
esac
