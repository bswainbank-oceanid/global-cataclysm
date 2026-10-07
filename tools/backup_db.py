"""
Copies the player database (accounts, games, chat: server/store.py) to a dated backup, safely while the
server is running (SQLite's own backup, not a file copy), and keeps only the newest few.

    python tools/backup_db.py                                  # server_data/backups/, the last 14 kept
    python tools/backup_db.py --db PATH --to DIR --keep 30

On the deployed server a systemd timer runs it every day (deploy/gc-backup.timer, docs/DEPLOY.md).
To restore: stop the server, copy a backup over the database file (deleting its -wal and -shm files), start it.
"""
import argparse
import datetime
import glob
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'server_data', 'global_cataclysm.sqlite3')
BACKUPS = os.path.join(ROOT, 'server_data', 'backups')
PREFIX = 'global_cataclysm-'


def backup(db, folder, keep, now=None):
    """Writes the backup; removes all but the newest `keep`. Returns the new file's path."""
    if not os.path.exists(db):
        raise FileNotFoundError(f'there is no database at {db}')
    os.makedirs(folder, exist_ok=True)
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    path = os.path.join(folder, f'{PREFIX}{stamp}.sqlite3')
    source = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    for old in sorted(glob.glob(os.path.join(folder, f'{PREFIX}*.sqlite3')))[:-keep]:
        os.remove(old)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default=DB)
    parser.add_argument('--to', default=BACKUPS)
    parser.add_argument('--keep', type=int, default=14)
    args = parser.parse_args(argv)
    path = backup(args.db, args.to, max(1, args.keep))
    print(f'backed up to {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
