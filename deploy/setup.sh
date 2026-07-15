#!/usr/bin/env bash
# Idempotent server bootstrap for Ubuntu 24.04 (Lightsail $5 or EC2 t4g.micro).
# Run as the default 'ubuntu' user. Safe to re-run.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y git curl sqlite3

# uv (python toolchain manager)
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Caddy (reverse proxy + automatic HTTPS) — official apt repo
# ref: https://caddyserver.com/docs/install#debian-ubuntu-raspbian
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update && sudo apt-get install -y caddy
fi

# App
if [ ! -d "$HOME/app" ]; then
  git clone git@github.com:YOURUSER/concert-reminder.git "$HOME/app"
fi
cd "$HOME/app"
uv sync

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ">>> Edit ~/app/.env with real tokens, then re-run this script."
  exit 0
fi

# Migrations (from Phase 2 onward)
uv run alembic upgrade head || echo "(no migrations yet — fine in Phase 1)"

# Services
sudo cp deploy/concert-reminder.service /etc/systemd/system/
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now concert-reminder
sudo systemctl reload caddy || sudo systemctl restart caddy
echo "done. logs: journalctl -u concert-reminder -f"
