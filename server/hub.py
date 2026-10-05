"""
The multi-player server's logic: many games at once, each connection a logged-in player, messages
routed by game and by player. Like GameHost (the one-game launcher's), it never touches a socket:
server/app.py hands it each message with the connection it came from, and delivers what it returns.

    hub = Hub(store)                           # loads every live game back from its save
    hub.connect(conn)                          # -> deliveries
    hub.handle(conn, msg)                      # -> deliveries: [(connections, message), ...]
    hub.runnable()                             # games that can move on by themselves
    hub.step(game_id)                          # -> deliveries of one step
    hub.disconnect(conn)

A connection first logs in (register, login, or token_login with a saved token), then enters a game it
plays in or hosts. Inside a game, a message naming a faction is only accepted from the player holding
that faction's seat. A game's broadcasts go to every connection in it; a message "to" a faction goes to
the connections of the player holding it; one marked "to_sender" goes back to the sender alone.

Client -> server (besides each game's own protocol, server/session.py's auto mode):
    {"type": "register", "player_name", "actual_name", "email", "password"}
    {"type": "login", "email", "password"}
    {"type": "token_login", "token"}
        -> {"type": "logged_in", "user": {...}, "token"}  (token_login: the same token back)
    {"type": "logout"}                          -> {"type": "logged_out"}; the token stops working
    {"type": "enter_game", "game_id"}           -> {"type": "entered_game", "game": {...summary...},
                                                    "seats": [...], "my_factions": [...]} and the game's
                                                    "feed" (auto mode's catch-up)
    {"type": "leave_game"}                      -> {"type": "left_game"}
Server -> client:
    {"type": "hello", "logged_in": false}       on connecting
    {"type": "error", "message", "problems"}    a request refused (to the sender only)
"""
import random

from . import accounts, games, persist
from .lobby import LobbyError, build_session
from .store import StoreError

ACCOUNT_TYPES = ('register', 'login', 'token_login', 'logout')
# A game's messages that a player may send without naming a faction of theirs.
FACTIONLESS = ('follow', 'watch', 'propose_armistice')


class Connection:
    """What the hub knows about one client connection."""

    def __init__(self, key):
        self.key = key            # whatever server/app.py identifies the socket by
        self.user = None          # the logged-in user (accounts.public), or None
        self.token = None
        self.game_id = None       # the game this connection is in, or None


class Hub:
    def __init__(self, store, repo=None):
        self.store = store
        self.repo = repo
        self.connections = {}     # key -> Connection
        self.sessions = {}        # game id -> persist.RecordedGame, for every live game
        self.load_problems = {}   # game id -> why its save couldn't be loaded
        for game_id in games.live_game_ids(store):
            self._load(game_id)

    # ---- connections -------------------------------------------------------------------------------

    def connect(self, key):
        conn = self.connections[key] = Connection(key)
        return [([conn.key], {'type': 'hello', 'logged_in': False})]

    def disconnect(self, key):
        self.connections.pop(key, None)

    def handle(self, key, msg):
        """Deliveries for one message from connection `key`."""
        conn = self.connections.get(key)
        if conn is None:
            conn = self.connections[key] = Connection(key)
        if not isinstance(msg, dict):
            return self._error(conn, 'a message must be a JSON object')
        kind = msg.get('type')
        try:
            if kind in ACCOUNT_TYPES:
                return self._account(conn, kind, msg)
            if conn.user is None:
                return self._error(conn, 'log in first')
            if kind == 'enter_game':
                return self._enter_game(conn, msg.get('game_id'))
            if kind == 'leave_game':
                conn.game_id = None
                return [([conn.key], {'type': 'left_game'})]
            if conn.game_id is not None:
                return self._game_message(conn, msg)
            return self._error(conn, f'unknown message type: {kind!r}')
        except (StoreError, LobbyError) as e:
            return self._error(conn, e.problems)

    # ---- accounts ----------------------------------------------------------------------------------

    def _account(self, conn, kind, msg):
        if kind == 'logout':
            accounts.logout(self.store, conn.token)
            conn.user = conn.token = conn.game_id = None
            return [([conn.key], {'type': 'logged_out'})]
        if kind == 'register':
            user, token = accounts.register(self.store, msg.get('player_name'), msg.get('actual_name'),
                                            msg.get('email'), msg.get('password'))
        elif kind == 'login':
            user, token = accounts.login(self.store, msg.get('email'), msg.get('password'))
        else:
            token = msg.get('token')
            user = accounts.login_with_token(self.store, token)
            if user is None:
                return self._error(conn, 'that login has ended: log in again')
        conn.user, conn.token, conn.game_id = user, token, None
        return [([conn.key], {'type': 'logged_in', 'user': user, 'token': token})]

    # ---- games -------------------------------------------------------------------------------------

    def start_game(self, game_id):
        """Builds the session for a game that has just gone live (games.create with fewer than two human
        seats, or games.launch), deals its factions and saves it. Returns the deliveries of its first
        steps' worth of nothing: the runner (runnable/step) takes it from there."""
        game = games.get(self.store, game_id)
        if game is None or game['status'] != games.LIVE:
            raise StoreError('only a live game can be started')
        settings = dict(game['settings'])
        settings.setdefault('seed', random.SystemRandom().randrange(2 ** 31))  # (recorded: the game can be replayed)
        session, seats = build_session(settings, auto=True, max_humans=None)
        recorded = persist.RecordedGame(session, persist.setup_for(session, seats, settings))
        games.record_factions(self.store, game_id, {s['seat']: s['faction'] for s in seats})
        self.sessions[game_id] = recorded
        self._save(game_id)
        return recorded

    def runnable(self):
        """The live games that can move on by themselves right now."""
        return [gid for gid, game in self.sessions.items() if game.can_step()]

    def step(self, game_id):
        """One step of a game that can move on; its broadcasts, routed. Saves at each turn start and
        whenever the game stops (waiting for a human, or over)."""
        game = self.sessions.get(game_id)
        if game is None:
            return []
        before = game.snapshots_taken
        out = game.step()
        if out is None:
            return []
        if game.snapshots_taken != before or not game.can_step():
            self._save(game_id)
        return self._route(game_id, None, out)

    def _enter_game(self, conn, game_id):
        game = games.get(self.store, game_id)
        if game is None:
            return self._error(conn, f'there is no game {game_id!r}')
        if not self._takes_part(conn.user['id'], game):
            return self._error(conn, 'you are not playing in that game')
        if game['status'] != games.LIVE:
            return self._error(conn, f"that game is {game['status']}")
        if game_id not in self.sessions:
            problem = self.load_problems.get(game_id, 'it is not running')
            return self._error(conn, f"that game can't be played right now: {problem}")
        conn.game_id = game_id
        entered = {'type': 'entered_game', 'game': games.summary(self.store, game, conn.user['id']),
                   'seats': game['seats'], 'factions': game['factions'],
                   'my_factions': sorted(self._factions_of(game, conn.user['id']))}
        feed = self.sessions[game_id].handle_message({'type': 'follow', 'since': None})
        return [([conn.key], entered)] + self._route(game_id, conn, feed)

    def _game_message(self, conn, msg):
        game_id = conn.game_id
        game = games.get(self.store, game_id)
        session = self.sessions.get(game_id)
        if game is None or session is None or game['status'] != games.LIVE:
            conn.game_id = None
            return self._error(conn, 'that game is no longer running')
        faction = msg.get('faction')
        mine = self._factions_of(game, conn.user['id'])
        if faction is not None and faction not in mine:
            return self._error(conn, f'you are not playing {faction}')
        if faction is None and msg.get('type') not in FACTIONLESS:
            return self._error(conn, 'say which of your factions this is for')
        if msg.get('type') == 'propose_armistice' and faction is None and game['host_id'] != conn.user['id']:
            return self._error(conn, 'only the host can propose an armistice without a faction')
        out = session.handle_message(msg)
        if msg.get('type') not in ('follow', 'watch'):
            self._save(game_id)
        return self._route(game_id, conn, out)

    # ---- helpers -----------------------------------------------------------------------------------

    def _load(self, game_id):
        save = games.load_state(self.store, game_id)
        try:
            if save is None:
                raise ValueError('it has no save')
            self.sessions[game_id] = persist.load(save['session'])
        except Exception as e:  # (one unreadable game mustn't stop the server: it is reported instead)
            self.load_problems[game_id] = str(e) or type(e).__name__

    def _save(self, game_id):
        game = self.sessions[game_id]
        gs = game.engine.game_state
        record = games.get(self.store, game_id)
        if record is None or record['status'] != games.LIVE:
            return
        games.save_state(self.store, game_id, game.doc())
        users = self._users_by_faction(record)
        games.update_progress(self.store, game_id, {
            'round': gs.round_number, 'turn': gs.global_turn, 'active_faction': gs.active_faction,
            'waiting_for': sorted({users[f] for f in game.waiting_for() if f in users})})
        if gs.game_over:
            games.finish(self.store, game_id)

    def _route(self, game_id, sender, messages):
        """(connections, message) pairs for a game's outgoing messages."""
        in_game = [c for c in self.connections.values() if c.game_id == game_id]
        record = games.get(self.store, game_id)
        users = self._users_by_faction(record) if record else {}
        out = []
        for m in messages:
            if m.get('to_sender'):
                targets = [sender.key] if sender is not None else []
            elif m.get('to'):
                owner = users.get(m['to'])
                targets = [c.key for c in in_game if c.user and c.user['id'] == owner]
            else:
                targets = [c.key for c in in_game]
            if targets:
                out.append((targets, m))
        return out

    @staticmethod
    def _takes_part(user_id, game):
        return game['host_id'] == user_id or any(s['user_id'] == user_id for s in game['seats'])

    @staticmethod
    def _factions_of(game, user_id):
        return {game['factions'][str(s['seat'])] for s in game['seats']
                if s['user_id'] == user_id and str(s['seat']) in game['factions']}

    @staticmethod
    def _users_by_faction(game):
        return {game['factions'][str(s['seat'])]: s['user_id'] for s in game['seats']
                if s['user_id'] is not None and str(s['seat']) in game['factions']}

    @staticmethod
    def _error(conn, problems):
        problems = [problems] if isinstance(problems, str) else list(problems)
        return [([conn.key], {'type': 'error', 'message': '; '.join(problems), 'problems': problems})]

