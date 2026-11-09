#!/bin/sh
# Copy tracked files from the Pi into this repo (Pi -> repo), e.g. after editing on the Pi.
# Review with git diff and commit. To send changes to the Pi use ./deploy.sh.
set -eu
cd "$(dirname "$0")"
PI=lbx@homepi.tail04e168.ts.net
RSYNC=$(command -v /opt/homebrew/bin/rsync || command -v rsync)
case "${1:-pull}" in
	pull) "$RSYNC" -a --prune-empty-dirs --filter="merge pi/sync-filter.txt" "$PI:/home/lbx/" pi/home/ ;;
	*) echo "usage: $0 [pull]   (deploy with ./deploy.sh)" >&2; exit 1 ;;
esac
