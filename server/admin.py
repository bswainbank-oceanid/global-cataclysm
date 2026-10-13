"""
What an admin manages from the main menu's Admin panel (docs/DATA_MODEL.md, "Player data"): the server's
numbers, its settings (the most players it accepts), finding a player, and locking an account. Every
function here is for admins only; server/hub.py checks that before calling.
"""
from . import accounts, games
from .store import StoreError, utc_now

DEFAULT_MAX_USERS = 100
MAX_MAX_USERS = 100000


def setting(store, key, default=None):
    doc = store.fetch('server_settings', key=key)
    return doc['value'] if doc else default


def set_setting(store, key, value):
    store.upsert('server_settings', {'key': key, 'value': value, 'saved': utc_now()}, key=key)


def max_users(store):
    return int(setting(store, 'max_users', DEFAULT_MAX_USERS))


def set_max_users(store, n):
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_MAX_USERS:
        raise StoreError(f'the most players must be a whole number from 1 to {MAX_MAX_USERS}')
    set_setting(store, 'max_users', n)
    return n


def player_count(store):
    """The players counted against max_users: every account that isn't locked."""
    return sum(1 for u in store.fetch_all('users') if not u.get('locked'))


def stats(store):
    """{players (not locked), locked (accounts), games_played (every game that started: live or finished; not
    lobbies), games_completed, games_live, max_users}."""
    rows = store.fetch_all('games')

    def count(*statuses):
        return sum(1 for g in rows if g['status'] in statuses)

    users = store.fetch_all('users')
    return {'players': sum(1 for u in users if not u.get('locked')), 'locked': sum(1 for u in users if u.get('locked')),
            'games_played': count(games.LIVE, games.FINISHED),
            'games_completed': count(games.FINISHED), 'games_live': count(games.LIVE), 'max_users': max_users(store)}


def find_users(store, query, limit=25):
    """Players whose player name, actual name or email contains `query` (any case; empty: everyone), as
    the lookup shows them: {id, player_name, actual_name, email, admin, locked, lock (why, when, by whom:
    accounts.py), created, last_login, games: {forming, live, finished}}."""
    q = str(query or '').strip().lower()
    users = store.fetch_all('users')
    hits = [u for u in users if not q or q in u['player_name'].lower() or q in u.get('actual_name', '').lower()
            or q in u['email'].lower()]
    hits.sort(key=lambda u: u['player_name'].lower())
    all_games = store.fetch_all('games')
    out = []
    for u in hits[:limit]:
        mine = [g for g in all_games if g['host_id'] == u['id'] or any(s['user_id'] == u['id'] for s in g['seats'])]
        out.append({'id': u['id'], 'player_name': u['player_name'], 'actual_name': u.get('actual_name', ''),
                    'email': u['email'], 'admin': bool(u.get('admin')), 'locked': bool(u.get('locked')),
                    'lock': u.get('lock') if u.get('locked') else None,
                    'created': u.get('created'), 'last_login': u.get('last_login'),
                    'games': {s: sum(1 for g in mine if g['status'] == s)
                              for s in (games.FORMING, games.LIVE, games.FINISHED)}})
    return out


def set_locked(store, user_id, locked, by=None):
    """Locks an account -- it still logs in, but can't take part in games -- or unlocks it; `by`: the admin's
    player name, kept with the lock (accounts.py). Returns the user. (Its seats in live games are handed to
    bots, and in forming ones given up, by the hub: server/hub.py.)"""
    with store.transaction():
        user = store.fetch('users', id=user_id)
        if user is None:
            raise StoreError(f'there is no player {user_id!r}')
        user['locked'] = bool(locked)
        if locked:
            user['lock'] = {'reason': accounts.LOCK_ADMIN, 'at': utc_now(), 'by': by}
        else:
            user.pop('lock', None)
        store.update('users', user, {'id': user_id})
    return user
