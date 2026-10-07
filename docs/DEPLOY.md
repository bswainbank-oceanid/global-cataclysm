# Deploying Global Cataclysm: 1972

How to run the game server on the internet and give players a client to download. The pieces:

| Piece | What it is | Where it lives |
|---|---|---|
| Game server | `python -m server.app` (multi-player), as a systemd service | `/opt/global-cataclysm` on a Linux server |
| Caddy | Web server in front of it: HTTPS certificates, the `wss://` connection, the download page | `/etc/caddy/Caddyfile` |
| Player database | Accounts, games, chat (SQLite), backed up daily | `/opt/global-cataclysm/server_data/` |
| Client in the browser | The web build of the client (`tools/build_client.py --web`) | `https://<domain>/play/` |
| Client to download | A Windows program built by `tools/build_client.py` | `https://<domain>/download/GlobalCataclysm.zip` |

```
player's client ──wss://<domain>/ws──▶ Caddy (443) ──ws://127.0.0.1:8765──▶ game server ──▶ SQLite
player's browser ──https://<domain>/──▶ Caddy ──▶ /var/www/global-cataclysm (download page, /play/, zips)
browser client ──wss://<domain>/ws──▶ (the same as the downloaded client)
```

## What you need

1. **A Linux server** (VPS): Ubuntu 24.04, 1 CPU and 1–2 GB of memory is plenty. Hetzner, DigitalOcean and
   Linode all work, for about $5–7 a month. Open ports 22 (SSH), 80 and 443 in its firewall.
2. **A domain name** (about $10–15 a year), e.g. `play.example.com`. Add an `A` record pointing it at the
   server's IP address, and wait until `ping play.example.com` shows that address.
3. **Access to the code** from the server. If the GitHub repository is private, add a
   [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
   and use the `git@github.com:...` address below.
4. **On your own PC, for the client:** Godot 4.7.2 with its export templates (Godot editor → Editor →
   Manage Export Templates → Download and Install).

## First-time setup

On the server, as a user with `sudo`:

```bash
curl -O https://raw.githubusercontent.com/OWNER/global-cataclysm/master/deploy/install.sh   # or copy it over
sudo bash install.sh play.example.com https://github.com/OWNER/global-cataclysm.git
```

`deploy/install.sh` installs Caddy, git and Python 3.14 (through [uv](https://docs.astral.sh/uv/)), makes a
`gc` user that owns the game, clones the code into `/opt/global-cataclysm`, and starts:

- `global-cataclysm.service`: the game server, on 127.0.0.1:8765 behind Caddy (`--behind-proxy`, so the
  login limits see each player's real address), telling clients the download page's address.
- `gc-backup.timer`: a database backup every day at about 04:30, the last 14 kept in `server_data/backups/`.
- Caddy, with an HTTPS certificate for the domain (it renews it by itself).

Then:

1. **Build and upload the client** (below).
2. **Register your own account** in the client.
3. **Make yourself an admin** on the server:
   ```bash
   sudo -u gc /opt/global-cataclysm/.venv/bin/python /opt/global-cataclysm/tools/set_admin.py \
       --db /opt/global-cataclysm/server_data/global_cataclysm.sqlite3 you@example.com
   ```
4. **Set up email** for password resets (when that feature lands): put `email.json` in
   `/opt/global-cataclysm/server_data/` (owned by `gc`) and restart the server.

## Building and publishing the client

On your PC, with everything committed and pushed:

```bash
python tools/build_client.py --server-url wss://play.example.com/ws
```

That syncs the client's data, builds `exports/client/GlobalCataclysm.exe` with the server's address built in,
and zips it as `exports/GlobalCataclysm-Build<N>.zip`. Upload it as the current download:

```bash
scp exports/GlobalCataclysm-Build<N>.zip you@play.example.com:/tmp/GlobalCataclysm.zip
ssh you@play.example.com "sudo cp /tmp/GlobalCataclysm.zip /var/www/global-cataclysm/download/"
```

Players download it from `https://play.example.com/`. Windows warns about an unknown publisher (the program
isn't code-signed): **More info → Run anyway**.

A development copy is unaffected: without the built-in address it still connects only with `--server`.

### The browser version

```bash
python tools/build_client.py --web
```

That builds `exports/web/` (an `index.html` and the engine and game files, about 60 MB, about 30 MB once
compressed in transit). It connects to the server it is loaded from (`wss://<domain>/ws`), so one build works
for any domain. Upload it, replacing the old one:

```bash
scp exports/web/* you@play.example.com:/tmp/gc-web/      # (make /tmp/gc-web first: ssh ... mkdir -p /tmp/gc-web)
ssh you@play.example.com "sudo rm -rf /var/www/global-cataclysm/play/* && sudo cp /tmp/gc-web/* /var/www/global-cataclysm/play/"
```

Players open `https://play.example.com/play/` (the download page links to it). Browsers check for a new
version on each visit (deploy/Caddyfile), so a reload after an update gets it; a player whose page is out of
date sees a **Reload for the new version** button.

To try a web build on your own PC: run a test game server (`python -m server.app --port 8796 --db <a test
database>`), serve the files (`python -m http.server 8800 --directory exports/web`), and open
`http://localhost:8800/?server=ws://localhost:8796/ws`.

## Updating

1. Push the new code from your PC.
2. On the server: `sudo bash /opt/global-cataclysm/deploy/update.sh`. It backs up the database, pulls the
   code, updates the Python packages and restarts the server. Live games carry on where they were.
3. If the client changed, build and upload new ones (above): the browser version and the Windows download.
   Players with an old one see their build differs from the server's, with a button to reload or download.

The client and server compare build numbers (git commit counts), so build the client from the same commit
the server runs.

## Looking after it

| Task | Command (on the server) |
|---|---|
| Is it running? | `systemctl status global-cataclysm` |
| Its log, live | `journalctl -u global-cataclysm -f` |
| Restart it | `sudo systemctl restart global-cataclysm` |
| Back up now | `sudo -u gc /opt/global-cataclysm/.venv/bin/python /opt/global-cataclysm/tools/backup_db.py` |
| When backups run | `systemctl list-timers gc-backup.timer` |
| Caddy's log | `/var/log/caddy/global-cataclysm.log` |

**Restoring a backup:** stop the server, copy the backup over `server_data/global_cataclysm.sqlite3`, delete
the `-wal` and `-shm` files next to it, start the server.

**Copying backups off the server** is worth doing now and then (a backup on the same disk doesn't survive
losing the server): `scp you@play.example.com:/opt/global-cataclysm/server_data/backups/* .`

## Security notes

- The game server listens only on 127.0.0.1; everything reaches it through Caddy over HTTPS.
- Passwords are stored as salted PBKDF2 hashes and login tokens only as hashes (`server/accounts.py`).
- Failed logins are limited per address (10 per 15 minutes) and per account (5 per 15 minutes), and new
  accounts per address (5 an hour): `server/limits.py`.
- The Admin panel's **Most players** setting caps how many accounts the server takes (100 by default).
- Keep the server updated: `sudo apt update && sudo apt upgrade` now and then.
