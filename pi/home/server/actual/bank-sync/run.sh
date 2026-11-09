#!/bin/sh
cd "$(dirname "$0")"
docker run --rm --network host --env-file .env -v "$PWD":/app -w /app node:22-alpine node sync.mjs
status=$?
./stats.sh
exit $status
