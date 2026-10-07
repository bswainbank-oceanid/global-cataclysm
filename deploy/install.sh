#!/usr/bin/env bash
# First-time setup of a game server on a fresh Ubuntu 24.04 machine (docs/DEPLOY.md). Run as root:
#
#   sudo bash install.sh play.example.com https://github.com/OWNER/global-cataclysm.git
#
# It installs Caddy, git and Python 3.14 (through uv), makes a `gc` user, puts the code in
# /opt/global-cataclysm, and starts the game server, its daily backup, and Caddy (HTTPS for the domain).
# The domain's DNS must already point at this machine, and ports 80 and 443 be open, for Caddy to get
# its certificate. Safe to run again: it updates what is there.
set -euo pipefail

DOMAIN="${1:?usage: install.sh DOMAIN REPO_URL}"
REPO="${2:?usage: install.sh DOMAIN REPO_URL}"
APP=/opt/global-cataclysm
WWW=/var/www/global-cataclysm

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi

echo "== packages"
apt-get update -q
apt-get install -y -q git curl debian-keyring debian-archive-keyring apt-transport-https sqlite3
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

echo "== the gc user and the code"
id gc >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/gc --shell /bin/bash gc
if [[ ! -d $APP/.git ]]; then
  git clone "$REPO" "$APP"
fi
chown -R gc:gc "$APP"
sudo -u gc git -C "$APP" pull --ff-only

echo "== Python 3.14 and the server's packages"
sudo -u gc bash -lc 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u gc bash -lc "cd $APP && ~/.local/bin/uv python install 3.14 && ~/.local/bin/uv venv --python 3.14 --allow-existing .venv \
  && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt"
sudo -u gc mkdir -p "$APP/server_data"

echo "== the download page"
mkdir -p "$WWW/download" "$WWW/play"
if [[ ! -f $WWW/index.html ]]; then
  sed "s/GC_DOMAIN/$DOMAIN/g" "$APP/deploy/www/index.html" > "$WWW/index.html"
fi

echo "== services"
sed "s/GC_DOMAIN/$DOMAIN/g" "$APP/deploy/global-cataclysm.service" > /etc/systemd/system/global-cataclysm.service
cp "$APP/deploy/gc-backup.service" "$APP/deploy/gc-backup.timer" /etc/systemd/system/
sed "s/GC_DOMAIN/$DOMAIN/g" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
systemctl daemon-reload
systemctl enable --now global-cataclysm gc-backup.timer
systemctl restart global-cataclysm
systemctl reload caddy || systemctl restart caddy

echo
echo "Done. The game server: systemctl status global-cataclysm   (log: journalctl -u global-cataclysm -f)"
echo "Players connect to wss://$DOMAIN/ws; the download page is https://$DOMAIN/ (the browser version: /play/)"
echo "Next: register your account in the client, then make it an admin:"
echo "  sudo -u gc $APP/.venv/bin/python $APP/tools/set_admin.py --db $APP/server_data/global_cataclysm.sqlite3 YOUR_EMAIL"
