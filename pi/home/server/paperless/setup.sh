#!/bin/bash
# One-time Paperless setup on the Pi. Run it yourself, interactively:
#   bash ~/server/paperless/setup.sh
# It asks you for the admin password; no secret is printed.
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p data media export consume redis
if [ ! -f .env ]; then
	(umask 077; echo "PAPERLESS_SECRET_KEY=$(openssl rand -hex 32)" > .env)
fi

docker compose up -d
echo "Waiting for Paperless (the first start installs Polish OCR and can take a few minutes)..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/accounts/login/)" = 200 ]; do sleep 5; done

if docker compose exec -T paperless python3 manage.py shell -c \
	"from django.contrib.auth.models import User; exit(0 if User.objects.filter(username='lbx').exists() else 1)"; then
	echo "Paperless user lbx already exists."
else
	echo; echo "Create your Paperless login (user lbx). Email can be left empty."
	docker compose exec paperless python3 manage.py createsuperuser --username lbx
fi

# API token for Pandora, straight into the bot's env file
if grep -q '^PAPERLESS_TOKEN=' ~/.env; then
	echo "PAPERLESS_TOKEN already in ~/.env."
else
	TOKEN=$(docker compose exec -T paperless python3 manage.py drf_create_token lbx | awk '{print $3}')
	[ -n "$TOKEN" ] || { echo "Could not create the API token."; exit 1; }
	printf '\nPAPERLESS_TOKEN=%s\n' "$TOKEN" >> ~/.env
	echo "Saved PAPERLESS_TOKEN to ~/.env."
fi

# HTTPS for tailnet devices only, like Vaultwarden on :8443
sudo tailscale serve --bg --https=8444 http://127.0.0.1:8010 >/dev/null
sudo systemctl restart telebot

echo; echo "Done: https://homepi.tail04e168.ts.net:8444 (Tailscale devices only)."
echo "Send Pandora a photo or PDF of a receipt to try it."
