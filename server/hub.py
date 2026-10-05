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
                                                    "seats": [...], "my_factions": [...], "players":
                                                    {faction: player name}} and the game's
                                                    "feed" (auto mode's catch-up)
    {"type": "leave_game"}                      -> {"type": "left_game"}

  Scenarios (New Game, and an admin's Scenarios mode):
    {"type": "scenarios"}                       -> {"type": "scenarios", "scenarios": [...scenarios.listing...],
                                                    "new_scenario": {...lobby.generator_info...}, "admin": bool,
                                                    "selected": id | null}
    {"type": "save_settings", "scenario_id", "settings"}    a player's own settings for a built-in or shared one
    {"type": "reset_settings", "scenario_id"}               Reset Settings
    {"type": "save_scenario", "scenario": {id, name, description, settings}}   one of the player's own
    {"type": "delete_scenario", "id"}
    {"type": "save_shared", "scenario": {id, name, description, settings}}     admins only
    {"type": "delete_shared", "id"}                                            admins only
        each answered with "scenarios" ("selected": what was saved, or null)

  Games:
    {"type": "create_game", "scenario": {"kind", "id"}, "settings"}
        Fewer than two HUMAN seats: the game starts at once and the sender is put in it ("entered_game"
        and the feed). Otherwise its lobby opens with the sender in it ("game_lobby").
    {"type": "my_games"}                        -> {"type": "my_games", "games": [...]}  (live and forming)
    {"type": "browse"}                          -> {"type": "open_games", "games": [...], "chat": [...]};
                                                   from then on "open_games" again whenever the list
                                                   changes, and the browse room's "chat"
    {"type": "stop_browsing"}
    {"type": "join_code", "code"}               a forming game: its lobby; a live one the player is in: the game
    {"type": "enter_lobby", "game_id"}          -> {"type": "game_lobby", "game": {...}, "chat": [...]}; then
                                                   "game_lobby" again whenever it changes
    {"type": "take_seat", "seat"} / {"type": "leave_seat", "seat"}       in the lobby the sender is in
    {"type": "launch"}                          the host: the game starts; everyone in the lobby gets
                                                   {"type": "game_launched", "game_id"} (then enter_game)
    {"type": "cancel"}                          the host: everyone in the lobby gets {"type": "game_cancelled"}
    {"type": "leave_lobby"}
    {"type": "chat", "room": "browse" | <game id>, "text"}
        -> {"type": "chat", "message": {...}} to everyone browsing (browse) or in that lobby
Server -> client:
    {"type": "hello", "logged_in": false}       on connecting
    {"type": "error", "message", "problems"}    a request refused (to the sender only)
"""
import random

from engine.repository import default_repository

from . import accounts, chat, games, persist, scenarios, setups
from .lobby import LobbyError, build_session, generator_info
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
        self.lobby_id = None      # the game lobby this connection is in, or None
        self.browsing = False     # looking at Available Games (gets its updates and its chat)


class Hub:
    def __init__(self, store, repo=None):
        self.store = store
        self.repo = repo or default_repository()
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
            handler = self._HANDLERS.get(kind)
            if handler is not None:
                return handler(self, conn, msg)
            if conn.game_id is not None:
                return self._game_message(conn, msg)
            return self._error(conn, f'unknown message type: {kind!r}')
        except (StoreError, LobbyError) as e:
            return self._error(conn, e.problems)

    # ---- accounts ----------------------------------------------------------------------------------

    def _account(self, conn, kind, msg):
        if kind == 'logout':
            accounts.logout(self.store, conn.token)
            conn.user = conn.token = conn.game_id = conn.lobby_id = None
            conn.browsing = False
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
        conn.lobby_id, conn.browsing = None, False
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
        conn.game_id, conn.lobby_id, conn.browsing = game_id, None, False
        players = {}
        for faction, uid in self._users_by_faction(game).items():
            user = accounts.get_user(self.store, uid)
            players[faction] = user['player_name'] if user else None
        entered = {'type': 'entered_game', 'game': games.summary(self.store, game, conn.user['id']),
                   'seats': game['seats'], 'factions': game['factions'], 'players': players,
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

    # ---- scenarios ---------------------------------------------------------------------------------

    def _scenarios(self, conn, msg, selected=None):
        uid = conn.user['id']
        return [([conn.key], {'type': 'scenarios', 'scenarios': scenarios.listing(self.store, uid, self.repo),
                              'new_scenario': generator_info(), 'admin': accounts.is_admin(self.store, uid),
                              'selected': selected})]

    def _save_settings(self, conn, msg):
        scenarios.save_settings(self.store, conn.user['id'], msg.get('scenario_id'), msg.get('settings'), self.repo)
        return self._scenarios(conn, msg, msg.get('scenario_id'))

    def _reset_settings(self, conn, msg):
        scenarios.reset_settings(self.store, conn.user['id'], msg.get('scenario_id'))
        return self._scenarios(conn, msg, msg.get('scenario_id'))

    def _save_scenario(self, conn, msg):
        sid = scenarios.save_own(self.store, conn.user['id'], msg.get('scenario') or {})
        return self._scenarios(conn, msg, sid)

    def _delete_scenario(self, conn, msg):
        scenarios.delete_own(self.store, conn.user['id'], msg.get('id'))
        return self._scenarios(conn, msg)

    def _save_shared(self, conn, msg):
        sid = scenarios.save_shared(self.store, conn.user['id'], msg.get('scenario') or {}, self.repo)
        return self._scenarios(conn, msg, sid)

    def _delete_shared(self, conn, msg):
        scenarios.delete_shared(self.store, conn.user['id'], msg.get('id'), self.repo)
        return self._scenarios(conn, msg)

    # ---- game lobbies ------------------------------------------------------------------------------

    def _create_game(self, conn, msg):
        uid = conn.user['id']
        scenario = self._scenario_ref(uid, msg.get('scenario') or {})
        game = games.create(self.store, uid, scenario, msg.get('settings'))
        if game['status'] == games.LIVE:
            self.start_game(game['id'])
            return self._enter_game(conn, game['id'])
        conn.lobby_id, conn.browsing = game['id'], False
        return [([conn.key], self._lobby_view(game))] + self._open_games_update()

    def _scenario_ref(self, user_id, ref):
        """{kind, id, name} for the scenario a game starts from, named by the server, not the client."""
        kind, sid = ref.get('kind'), ref.get('id')
        if kind in ('fixed', 'new'):
            return {'kind': kind, 'id': kind, 'name': None}
        if kind == 'shared':
            row = next((r for r in setups.listing(self.repo) if r['id'] == sid), None)
            if row is None:
                raise StoreError(f'there is no shared scenario {sid!r}')
            return {'kind': kind, 'id': sid, 'name': row['name']}
        if kind == 'own':
            doc = scenarios.get_own(self.store, sid)
            if doc is None or doc['owner_id'] != user_id:
                raise StoreError(f'you have no scenario {sid!r}')
            return {'kind': kind, 'id': sid, 'name': doc['name']}
        raise StoreError('a game needs the scenario it starts from (fixed, new, shared or own)')

    def _my_games(self, conn, msg):
        mine = games.my_games(self.store, conn.user['id'], (games.LIVE, games.FORMING))
        for g in mine:
            g['loadable'] = g['status'] != games.LIVE or g['id'] in self.sessions
        return [([conn.key], {'type': 'my_games', 'games': mine})]

    def _browse(self, conn, msg):
        conn.browsing, conn.lobby_id = True, None
        return [([conn.key], {'type': 'open_games', 'games': games.open_games(self.store),
                              'chat': chat.recent(self.store, chat.BROWSE)})]

    def _stop_browsing(self, conn, msg):
        conn.browsing = False
        return []

    def _join_code(self, conn, msg):
        game = games.find_by_code(self.store, msg.get('code'))
        if game is None:
            return self._error(conn, 'no game has that code')
        if game['status'] == games.FORMING:
            return self._enter_lobby(conn, {'game_id': game['id']})
        if game['status'] == games.LIVE:
            return self._enter_game(conn, game['id'])
        return self._error(conn, f"that game is {game['status']}")

    def _enter_lobby(self, conn, msg):
        game = games.get(self.store, msg.get('game_id'))
        if game is None:
            return self._error(conn, f"there is no game {msg.get('game_id')!r}")
        if game['status'] != games.FORMING:
            return self._error(conn, 'that game is not forming any more' if game['status'] == games.LIVE
                               else f"that game is {game['status']}")
        conn.lobby_id, conn.browsing, conn.game_id = game['id'], False, None
        return [([conn.key], self._lobby_view(game))]

    def _leave_lobby(self, conn, msg):
        conn.lobby_id = None
        return [([conn.key], {'type': 'left_lobby'})]

    def _take_seat(self, conn, msg):
        return self._seat_change(conn, msg, games.take_seat)

    def _leave_seat(self, conn, msg):
        return self._seat_change(conn, msg, games.leave_seat)

    def _seat_change(self, conn, msg, change):
        if conn.lobby_id is None:
            return self._error(conn, 'enter a game lobby first')
        seat = msg.get('seat')
        if not isinstance(seat, int) or isinstance(seat, bool):
            return self._error(conn, 'which seat? (a number)')
        game = change(self.store, conn.lobby_id, conn.user['id'], seat)
        return self._lobby_update(game) + self._open_games_update()

    def _launch(self, conn, msg):
        if conn.lobby_id is None:
            return self._error(conn, 'enter a game lobby first')
        game = games.launch(self.store, conn.lobby_id, conn.user['id'])
        self.start_game(game['id'])
        members = self._in_lobby(game['id'])
        for c in self.connections.values():
            if c.lobby_id == game['id']:
                c.lobby_id = None
        out = [(members, {'type': 'game_launched', 'game_id': game['id']})] if members else []
        return out + self._open_games_update()

    def _cancel(self, conn, msg):
        if conn.lobby_id is None:
            return self._error(conn, 'enter a game lobby first')
        game = games.cancel(self.store, conn.lobby_id, conn.user['id'])
        members = self._in_lobby(game['id'])
        for c in self.connections.values():
            if c.lobby_id == game['id']:
                c.lobby_id = None
        out = [(members, {'type': 'game_cancelled', 'game_id': game['id']})] if members else []
        return out + self._open_games_update()

    def _chat(self, conn, msg):
        room = msg.get('room')
        if room == chat.BROWSE:
            if not conn.browsing:
                return self._error(conn, 'browse the available games to chat there')
            targets = [c.key for c in self.connections.values() if c.browsing and c.user]
        else:
            if room is None or room != conn.lobby_id:
                return self._error(conn, "you can only chat in the lobby you're in")
            targets = self._in_lobby(room)
        message = chat.post(self.store, room, conn.user['id'], msg.get('text'))
        return [(targets, {'type': 'chat', 'message': message})]

    def _lobby_view(self, game):
        """What a game lobby shows: the game (its settings, seats with who holds each, its code) and its chat."""
        seats = []
        for s in game['seats']:
            user = accounts.get_user(self.store, s['user_id']) if s['user_id'] else None
            seats.append(dict(s, player_name=user['player_name'] if user else None))
        view = dict(games.summary(self.store, game), settings=game['settings'], seats=seats)
        return {'type': 'game_lobby', 'game': view, 'chat': chat.recent(self.store, game['id'])}

    def _lobby_update(self, game):
        members = self._in_lobby(game['id'])
        return [(members, self._lobby_view(game))] if members else []

    def _open_games_update(self):
        browsers = [c.key for c in self.connections.values() if c.browsing and c.user]
        if not browsers:
            return []
        return [(browsers, {'type': 'open_games', 'games': games.open_games(self.store)})]

    def _in_lobby(self, game_id):
        return [c.key for c in self.connections.values() if c.lobby_id == game_id and c.user]

    _HANDLERS = {
        'scenarios': _scenarios, 'save_settings': _save_settings, 'reset_settings': _reset_settings,
        'save_scenario': _save_scenario, 'delete_scenario': _delete_scenario,
        'save_shared': _save_shared, 'delete_shared': _delete_shared,
        'create_game': _create_game, 'my_games': _my_games, 'browse': _browse, 'stop_browsing': _stop_browsing,
        'join_code': _join_code, 'enter_lobby': _enter_lobby, 'leave_lobby': _leave_lobby,
        'take_seat': _take_seat, 'leave_seat': _leave_seat, 'launch': _launch, 'cancel': _cancel, 'chat': _chat,
    }

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

