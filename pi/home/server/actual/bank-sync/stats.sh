#!/bin/sh
cd "$(dirname "$0")"
exec docker run --rm --network host --env-file .env -e TZ=Europe/Warsaw -v "$PWD":/app -v /home/lbx/server/homepage/public:/homepage-public -w /app node:22-alpine node stats.mjs
