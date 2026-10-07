#!/usr/bin/env bash
# Puts the latest pushed code on the game server and restarts it (docs/DEPLOY.md). Run on the server:
#
#   sudo bash /opt/global-cataclysm/deploy/update.sh
#
# Live games are saved as they go, so they carry on after the restart. A backup is taken first.
set -euo pipefail
APP=/opt/global-cataclysm

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi
cd "$APP"
if [[ -f server_data/global_cataclysm.sqlite3 ]]; then
  sudo -u gc .venv/bin/python tools/backup_db.py --keep 14
fi
sudo -u gc git pull --ff-only
sudo -u gc bash -lc "cd $APP && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt -q"
systemctl restart global-cataclysm
sleep 3
systemctl --no-pager --lines=5 status global-cataclysm
