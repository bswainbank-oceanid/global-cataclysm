"""
Turns a player's admin flag on or off (admins get Scenarios mode, which edits the shared
scenarios for everyone). There's no in-game UI for this on purpose.

    python tools/set_admin.py ann@example.com          # by email or player name
    python tools/set_admin.py Ann --off
    python tools/set_admin.py --list                   # who is an admin
    python tools/set_admin.py Ann --db path/to/players.sqlite3

Run it while the server is stopped or running: the database copes with both.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import accounts  # noqa: E402
from server.store import DEFAULT_PATH, Store, StoreError  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('who', nargs='?', help="the player's email or player name")
    parser.add_argument('--off', action='store_true', help='take the admin flag away instead')
    parser.add_argument('--list', action='store_true', help='list the admins')
    parser.add_argument('--db', default=str(DEFAULT_PATH), help='the player database (default: %(default)s)')
    args = parser.parse_args(argv)
    if not args.list and not args.who:
        parser.error('name a player (email or player name), or use --list')
    if not os.path.exists(args.db):
        parser.error(f'there is no player database at {args.db}: start the server once, or pass --db')

    store = Store(args.db)
    try:
        if args.list:
            admins = [accounts.public(u) for u in store.fetch_all('users') if u.get('admin')]
            for u in admins:
                print(f"{u['id']}  {u['player_name']}  <{u['email']}>")
            if not admins:
                print('no admins')
            return 0
        user = accounts.find_user(store, args.who)
        if user is None:
            print(f'no player has the email or player name {args.who!r}', file=sys.stderr)
            return 1
        user = accounts.set_admin(store, user['id'], not args.off)
        print(f"{user['player_name']} <{user['email']}> is {'now an admin' if user['admin'] else 'no longer an admin'}")
        return 0
    except StoreError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    sys.exit(main())
