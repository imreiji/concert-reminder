#!/usr/bin/env bash
# Idempotent server bootstrap for Ubuntu 24.04 (Lightsail / EC2), behind Cloudflare.
# Run as the default 'ubuntu' user. Safe to re-run.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y git curl sqlite3

# uv (python toolchain manager)
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Caddy - official apt repo (https://caddyserver.com/docs/install#debian-ubuntu-raspbian)
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update && sudo apt-get install -y caddy
fi

# Cloudflare Origin certificate must exist before Caddy can start the site.
sudo mkdir -p /etc/caddy/certs
if [ ! -f /etc/caddy/certs/origin.pem ] || [ ! -f /etc/caddy/certs/origin.key ]; then
  echo ">>> Missing Cloudflare Origin certificate."
  echo ">>> Cloudflare dashboard -> SSL/TLS -> Origin Server -> Create Certificate"
  echo ">>> Paste cert into /etc/caddy/certs/origin.pem and key into /etc/caddy/certs/origin.key"
  echo ">>> then: sudo chown caddy:caddy /etc/caddy/certs/* && sudo chmod 600 /etc/caddy/certs/origin.key"
  echo ">>> and re-run this script."
  CERT_MISSING=1
fi

# App (private repo: needs a read-only Deploy Key - see docs/deploy.md step 2)
if [ ! -d "$HOME/app" ]; then
  git clone git@github.com:YOURUSER/concert-reminder.git "$HOME/app"
fi
cd "$HOME/app"
uv sync

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ">>> Edit ~/app/.env with production values, then re-run this script."
  exit 0
fi

uv run alembic upgrade head

sudo cp deploy/concert-reminder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now concert-reminder

if [ -z "${CERT_MISSING:-}" ]; then
  sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
  sudo systemctl reload caddy || sudo systemctl restart caddy
  echo "done. app logs: journalctl -u concert-reminder -f"
else
  echo "app is running on :8000 but Caddy is NOT serving yet (cert missing, see above)."
fi
