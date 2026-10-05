"""
Games as records in the player database (docs/DATA_MODEL.md, "Game" and "Game save"): from a lobby
that players take seats in, through a live game, to its end. This is the bookkeeping only -- who sits
where, what state a game is in, its saved state; the server builds and runs the game itself.

    game = create(store, host_id, {'kind': 'shared', 'id': 'Setup_002', 'name': 'Duel'}, settings)
    take_seat(store, game['id'], user_id, 2)
    launch(store, game['id'], host_id)          # once every HUMAN seat is taken
    record_factions(store, game['id'], {1: 'UER', 2: 'UE', ...})

A game with exactly one HUMAN seat skips the lobby (the host takes the seat and it is live at once);
so does one with none (all bots: the host owns it and watches). Every refusal is a StoreError.
"""
import secrets

from .lobby import check_settings
from .store import StoreError, utc_now

FORMING, LIVE, FINISHED, CANCELLED = 'forming', 'live', 'finished', 'cancelled'
STATUSES = (FORMING, LIVE, FINISHED, CANCELLED)
SCENARIO_KINDS = ('fixed', 'new', 'shared', 'own')
CODE_LENGTH = 6
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I/L look-alikes
HUMAN = 'HUMAN'


def create(store, host_id, scenario, settings, rng=None):
    """A new game of `settings` (validated as a game start's, any number of humans), started from
    `scenario` ({kind, id, name}) by `host_id`; returns it. Forming (in its lobby) when it has two or
    more HUMAN seats; otherwise live at once, the host in the one HUMAN seat if there is one."""
    if not isinstance(scenario, dict) or scenario.get('kind') not in SCENARIO_KINDS:
        raise StoreError(f'a game needs the scenario it starts from ({", ".join(SCENARIO_KINDS)})')
    problems = check_settings(settings, max_humans=None) if isinstance(settings, dict) else ['a game needs its settings']
    if problems:
        raise StoreError(problems)
    if store.fetch('users', id=host_id) is None:
        raise StoreError(f'there is no user {host_id!r}')
    seats = [{'seat': i, 'mode': s['mode'], 'faction': s.get('faction', 'random'), 'user_id': None}
             for i, s in enumerate(settings['seats'], 1)]
    humans = [s for s in seats if s['mode'] == HUMAN]
    now = utc_now()
    live = len(humans) <= 1
    if len(humans) == 1:
        humans[0]['user_id'] = host_id
    with store.transaction():
        gid = store.next_id('game')
        game = {'id': gid, 'code': None, 'host_id': host_id, 'status': LIVE if live else FORMING,
                'scenario': {'kind': scenario['kind'], 'id': scenario.get('id'), 'name': scenario.get('name')},
                'settings': settings, 'seats': seats, 'factions': {}, 'progress': None,
                'created': now, 'started': now if live else None, 'ended': None}
        for attempt in range(20):  # (a clash with a code in use is rare: just draw another)
            game['code'] = _new_code(rng)
            try:
                store.insert('games', game, id=gid, code=game['code'], host_id=host_id, status=game['status'])
                break
            except StoreError:
                if attempt == 19:
                    raise
    return game


def get(store, game_id):
    return store.fetch('games', id=game_id)


def find_by_code(store, code):
    """The game with invitation code `code` (any case, spaces ignored), or None."""
    code = ''.join(str(code or '').split()).upper()
    return store.fetch('games', code=code) if code else None


# ---- the lobby ---------------------------------------------------------------------------------------

def take_seat(store, game_id, user_id, seat):
    """`user_id` takes HUMAN seat `seat` (a number) of a forming game. A player may hold several seats;
    a seat someone else holds is refused. Returns the game."""
    with store.transaction():
        game = _forming(store, game_id)
        s = _seat(game, seat)
        if s['mode'] != HUMAN:
            raise StoreError(f'seat {seat} is not for a human player')
        if s['user_id'] not in (None, user_id):
            raise StoreError(f'seat {seat} is taken')
        s['user_id'] = user_id
        _save(store, game)
    return game


def leave_seat(store, game_id, user_id, seat):
    """`user_id` gives back seat `seat` of a forming game (only a seat they hold). Returns the game."""
    with store.transaction():
        game = _forming(store, game_id)
        s = _seat(game, seat)
        if s['user_id'] != user_id:
            raise StoreError(f'seat {seat} is not yours')
        s['user_id'] = None
        _save(store, game)
    return game


def open_seats(game):
    """The numbers of a game's HUMAN seats nobody holds yet."""
    return [s['seat'] for s in game['seats'] if s['mode'] == HUMAN and s['user_id'] is None]


def launch(store, game_id, user_id):
    """The host starts a forming game: every HUMAN seat must be taken. Returns the game, now live (the
    server then builds it, and records the factions dealt -- record_factions)."""
    with store.transaction():
        game = _forming(store, game_id)
        _host(game, user_id, 'launch')
        waiting = open_seats(game)
        if waiting:
            raise StoreError(f'every human seat must be taken first (open: {", ".join(map(str, waiting))})')
        game.update(status=LIVE, started=utc_now())
        _save(store, game)
    return game


def cancel(store, game_id, user_id):
    """The host calls off a forming game. Returns it, cancelled."""
    with store.transaction():
        game = _forming(store, game_id)
        _host(game, user_id, 'cancel')
        game.update(status=CANCELLED, ended=utc_now())
        _save(store, game)
    return game


# ---- a live game -------------------------------------------------------------------------------------

def record_factions(store, game_id, factions):
    """Which faction each seat plays ({seat: faction}), once the server has dealt them at the start."""
    with store.transaction():
        game = _status(store, game_id, LIVE)
        game['factions'] = {str(k): v for k, v in factions.items()}
        _save(store, game)
    return game


def update_progress(store, game_id, progress):
    """A live game's progress for My Live Games ({round, turn, active_faction, waiting_for: [user ids]}),
    copied after every phase."""
    with store.transaction():
        game = _status(store, game_id, LIVE)
        game['progress'] = progress
        _save(store, game)
    return game


def finish(store, game_id):
    """A live game has ended (a winner, an armistice, nobody left)."""
    with store.transaction():
        game = _status(store, game_id, LIVE)
        game.update(status=FINISHED, ended=utc_now())
        _save(store, game)
    return game


def save_state(store, game_id, session):
    """Writes a live game's complete saved state (what the server needs to carry on after a restart)."""
    with store.transaction():
        _status(store, game_id, LIVE)
        store.upsert('game_saves', {'game_id': game_id, 'saved': utc_now(), 'session': session}, game_id=game_id)


def load_state(store, game_id):
    """A game's saved state ({game_id, saved, session}), or None if it has none."""
    return store.fetch('game_saves', game_id=game_id)


# ---- lists -------------------------------------------------------------------------------------------

def open_games(store):
    """Available Games: every forming game with an open HUMAN seat, oldest first, each as summary()."""
    games = store.fetch_all('games', order='rowid', status=FORMING)
    return [summary(store, g) for g in games if open_seats(g)]


def my_games(store, user_id, statuses=(LIVE,)):
    """The games `user_id` hosts or holds a seat in, with one of `statuses` (default: My Live Games),
    newest first, each as summary(store, game, user_id)."""
    games = store.fetch_all('games', order='rowid DESC', status=list(statuses))
    return [summary(store, g, user_id) for g in games
            if g['host_id'] == user_id or any(s['user_id'] == user_id for s in g['seats'])]


def live_game_ids(store):
    """Every live game's id (to load back when the server starts)."""
    return [g['id'] for g in store.fetch_all('games', status=LIVE)]


def summary(store, game, user_id=None):
    """What a lobby list shows of a game: {id, code, status, scenario, host_id, host_name, humans,
    open_seats, progress}, plus with `user_id` that player's seats and their factions (when known)."""
    host = store.fetch('users', id=game['host_id'])
    out = {'id': game['id'], 'code': game['code'], 'status': game['status'], 'scenario': game['scenario'],
           'host_id': game['host_id'], 'host_name': host['player_name'] if host else None,
           'humans': sum(1 for s in game['seats'] if s['mode'] == HUMAN), 'open_seats': len(open_seats(game)),
           'progress': game['progress'], 'created': game['created']}
    if user_id is not None:
        mine = [s['seat'] for s in game['seats'] if s['user_id'] == user_id]
        out['my_seats'] = mine
        out['my_factions'] = [game['factions'].get(str(n)) or _known_faction(game, n) for n in mine]
    return out


# ---- helpers -----------------------------------------------------------------------------------------

def _new_code(rng):
    choice = rng.choice if rng is not None else secrets.choice
    return ''.join(choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _known_faction(game, seat):
    faction = _seat(game, seat)['faction']
    return None if faction == 'random' else faction


def _status(store, game_id, status):
    game = store.fetch('games', id=game_id)
    if game is None:
        raise StoreError(f'there is no game {game_id!r}')
    if game['status'] != status:
        raise StoreError(f"that game is {game['status']}, not {status}")
    return game


def _forming(store, game_id):
    game = store.fetch('games', id=game_id)
    if game is None:
        raise StoreError(f'there is no game {game_id!r}')
    if game['status'] != FORMING:
        raise StoreError({LIVE: 'that game has already started', FINISHED: 'that game is over',
                          CANCELLED: 'that game was cancelled'}[game['status']])
    return game


def _seat(game, seat):
    for s in game['seats']:
        if s['seat'] == seat:
            return s
    raise StoreError(f'there is no seat {seat!r}')


def _host(game, user_id, what):
    if game['host_id'] != user_id:
        raise StoreError(f'only the host can {what} the game')


def _save(store, game):
    store.update('games', game, {'id': game['id']}, status=game['status'])
