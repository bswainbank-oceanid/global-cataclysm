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
        -> {"type": "logged_in", "user": {...}, "token"}  (token_login: the same token back), or
           {"type": "login_failed", "message", "problems"}
    {"type": "logout"}                          -> {"type": "logged_out"}; the token stops working
    {"type": "enter_game", "game_id", ["since", "epoch"]}
                                                -> {"type": "entered_game", "game": {...summary...},
                                                    "seats": [...], "my_factions": [...], "players":
                                                    {faction: player name}} and the game's
                                                    "feed" (auto mode's catch-up: with "since" and
                                                    "epoch", what a reconnecting client missed)
    {"type": "leave_game"}                      -> {"type": "left_game"}
    {"type": "hand_over", "faction"}            the sender's faction is played by a bot from now on
    {"type": "replace_with_bot"}                the player whose turn it is has gone over the game's
                                                   "turn_hours": a bot takes their seat. The host may
                                                   ask, or anyone when the slow player is the host.
        Either way the game broadcasts {"type": "seat_changed", "faction", "reason": "handed_over" |
        "replaced" | "locked", "events"}, and the player who lost the seat (if they now have no part in
        the game) gets {"type": "left_game", "reason"}.

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

  In a game (chat.game_room / chat.ally_room):
    {"type": "game_chat", "channel": "game" | "ally", "text", ["faction"]}
        -> {"type": "game_chat", "channel", "alliance", "message": {...}} to everyone in the game (game),
           or to the players of the alliance's members ("ally": the alliance of "faction", one of the
           sender's own, or else of their first faction in one)
    {"type": "game_chat_history", ["faction"]}  -> {"type": "game_chat_history", "game": [...],
                                                   "ally": [...] | null, "alliance": tag | null}

  The Admin panel (admins only):
    {"type": "admin_overview"}                  -> {"type": "admin_overview", "stats": {players, games_played,
                                                   games_completed, games_live, max_users}, "build"}
    {"type": "admin_set_max_users", "max"}      -> "admin_overview"
    {"type": "admin_find", "query"}             -> {"type": "admin_users", "users": [...server/admin.py...]}
    {"type": "admin_lock", "user_id", "locked"} -> "admin_users" again (for the last query); a locked player
                                                   is logged out wherever they are connected
        -> {"type": "chat", "message": {...}} to everyone browsing (browse) or in that lobby
Server -> client:
    {"type": "hello", "logged_in": false, "build": {build, commit, label}}   on connecting (server/build.py)
    {"type": "error", "message", "problems"}    a request refused (to the sender only)
"""
import datetime
import random

from engine.repository import default_repository

from . import accounts, admin, chat, games, persist, scenarios, setups
from .build import build_info
from .lobby import LobbyError, build_session, generator_info
from .store import StoreError, utc_now

ACCOUNT_TYPES = ('register', 'login', 'token_login', 'logout')
# A game's messages that a player may send without naming a faction of theirs.
FACTIONLESS = ('follow', 'watch', 'propose_armistice')
# A game's messages only the server itself sends (server/session.py).
INTERNAL = ('convert_to_bot',)
SEAT_LOST = {'handed_over': 'you handed your seat over to a bot',
             'replaced': 'a bot has taken your seat: your turn went over the time limit',
             'locked': 'your account is locked'}


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
        return [([conn.key], {'type': 'hello', 'logged_in': False, 'build': build_info()})]

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
                try:
                    return self._account(conn, kind, msg)
                except StoreError as e:  # (its own type: the client never mistakes another error for it)
                    return [([conn.key], {'type': 'login_failed', 'message': '; '.join(e.problems), 'problems': e.problems})]
            if conn.user is None:
                return self._error(conn, 'log in first')
            if kind == 'enter_game':
                return self._enter_game(conn, msg.get('game_id'), msg.get('since'), msg.get('epoch'))
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
                raise StoreError('that login has ended: log in again')
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

    def _enter_game(self, conn, game_id, since=None, epoch=None):
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
                   'my_factions': sorted(self._factions_of(game, conn.user['id'])),
                   'turn_hours': int(game['settings'].get('turn_hours', 0) or 0)}
        feed = self.sessions[game_id].handle_message({'type': 'follow', 'since': since, 'epoch': epoch})
        return [([conn.key], entered)] + self._route(game_id, conn, feed)

    def _game_message(self, conn, msg):
        game_id = conn.game_id
        game = games.get(self.store, game_id)
        session = self.sessions.get(game_id)
        if game is None or session is None or game['status'] != games.LIVE:
            conn.game_id = None
            return self._error(conn, 'that game is no longer running')
        if msg.get('type') in INTERNAL:
            return self._error(conn, f"unknown message type: {msg.get('type')!r}")
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

    # ---- chat in a game ----------------------------------------------------------------------------

    def _game_chat(self, conn, msg):
        record = self._live_game_of(conn)
        channel = msg.get('channel')
        if channel == 'game':
            room, alliance = chat.game_room(record['id']), None
            targets = [c.key for c in self.connections.values() if c.game_id == record['id'] and c.user]
        elif channel == 'ally':
            alliance = self._alliance_of(record, conn.user['id'], msg.get('faction'))
            if alliance is None:
                raise StoreError('you are not in an alliance')
            room = chat.ally_room(record['id'], alliance)
            targets = [c.key for c in self.connections.values() if c.game_id == record['id'] and c.user
                       and alliance in self._alliances_of(record, c.user['id'])]
        else:
            raise StoreError("a game's chat is 'game' or 'ally'")
        message = chat.post(self.store, room, conn.user['id'], msg.get('text'))
        return [(targets, {'type': 'game_chat', 'channel': channel, 'alliance': alliance, 'message': message})]

    def _game_chat_history(self, conn, msg):
        record = self._live_game_of(conn)
        alliance = self._alliance_of(record, conn.user['id'], msg.get('faction'))
        ally = chat.recent(self.store, chat.ally_room(record['id'], alliance)) if alliance else None
        return [([conn.key], {'type': 'game_chat_history', 'game': chat.recent(self.store, chat.game_room(record['id'])),
                              'ally': ally, 'alliance': alliance})]

    def _alliances_of(self, record, user_id):
        """The alliance tags of `user_id`'s factions in a live game, as they stand now."""
        factions = self.sessions[record['id']].engine.game_state.factions
        return {factions[f].alliance for f in self._factions_of(record, user_id)
                if f in factions and factions[f].alliance}

    def _alliance_of(self, record, user_id, faction=None):
        """The alliance whose Ally Chat `user_id` sees: `faction`'s (one of theirs), or else their first
        faction's that is in one. None when it isn't in one."""
        mine = sorted(self._factions_of(record, user_id))
        if faction is not None:
            if faction not in mine:
                raise StoreError(f'you are not playing {faction}')
            mine = [faction]
        factions = self.sessions[record['id']].engine.game_state.factions
        return next((factions[f].alliance for f in mine if f in factions and factions[f].alliance), None)

    # ---- the Admin panel ---------------------------------------------------------------------------

    def _admin_only(self, conn):
        if not accounts.is_admin(self.store, conn.user['id']):
            raise StoreError('only an admin can do that')

    def _admin_overview(self, conn, msg):
        self._admin_only(conn)
        return [([conn.key], {'type': 'admin_overview', 'stats': admin.stats(self.store), 'build': build_info()})]

    def _admin_set_max_users(self, conn, msg):
        self._admin_only(conn)
        admin.set_max_users(self.store, msg.get('max'))
        return self._admin_overview(conn, msg)

    def _admin_find(self, conn, msg):
        self._admin_only(conn)
        conn.admin_query = str(msg.get('query') or '')
        return [([conn.key], {'type': 'admin_users', 'users': admin.find_users(self.store, conn.admin_query)})]

    def _admin_lock(self, conn, msg):
        self._admin_only(conn)
        user_id = msg.get('user_id')
        if user_id == conn.user['id']:
            raise StoreError("you can't lock your own account")
        locked = bool(msg.get('locked'))
        admin.set_locked(self.store, user_id, locked)
        out = []
        if locked:
            for c in self.connections.values():  # logged out wherever they are
                if c.user and c.user['id'] == user_id:
                    c.user = c.token = c.game_id = c.lobby_id = None
                    c.browsing = False
                    out.append(([c.key], {'type': 'logged_out', 'reason': accounts.LOCKED}))
            out += self._hand_seats_to_bots(user_id)
        return out + self._admin_find(conn, {'query': getattr(conn, 'admin_query', '')})

    def _hand_seats_to_bots(self, user_id):
        """A locked player's seats in live games go to bots."""
        out = []
        for game_id in list(self.sessions):
            record = games.get(self.store, game_id)
            if record is None or record['status'] != games.LIVE:
                continue
            for faction in sorted(self._factions_of(record, user_id)):
                out += self._seat_to_bot(game_id, faction, 'locked')
        return out

    # ---- seats handed over to bots ------------------------------------------------------------------

    def _hand_over(self, conn, msg):
        record = self._live_game_of(conn)
        faction = msg.get('faction')
        if faction not in self._factions_of(record, conn.user['id']):
            raise StoreError(f'you are not playing {faction}')
        return self._seat_to_bot(record['id'], faction, 'handed_over')

    def _replace_with_bot(self, conn, msg):
        record = self._live_game_of(conn)
        hours = int(record['settings'].get('turn_hours', 0) or 0)
        if not hours:
            raise StoreError('this game has no time limit on turns')
        session = self.sessions[record['id']]
        faction = session.engine.game_state.active_faction
        owner = self._users_by_faction(record).get(faction)
        if owner is None or faction not in session.waiting_for():
            raise StoreError('nobody is keeping the game waiting')
        if owner == conn.user['id']:
            raise StoreError('it is your own turn (hand your seat over instead)')
        if conn.user['id'] != record['host_id'] and owner != record['host_id']:
            raise StoreError('only the host can replace a player (anyone can when the host is the slow one)')
        left = self.turn_time_left(record, hours)
        if left > 0:
            raise StoreError(f'{faction} still has {_duration(left)} to take their turn')
        return self._seat_to_bot(record['id'], faction, 'replaced')

    @staticmethod
    def turn_time_left(record, hours, now=None):
        """Seconds left of the current turn's `hours` (from when it began, progress.turn_started)."""
        started = (record.get('progress') or {}).get('turn_started')
        if not started:
            return 0
        now = now or datetime.datetime.now(datetime.timezone.utc)
        elapsed = (now - datetime.datetime.fromisoformat(started)).total_seconds()
        return max(0, hours * 3600 - elapsed)

    def _live_game_of(self, conn):
        record = games.get(self.store, conn.game_id) if conn.game_id else None
        if record is None or record['status'] != games.LIVE or conn.game_id not in self.sessions:
            raise StoreError('you are not in a live game')
        return record

    def _seat_to_bot(self, game_id, faction, reason):
        """`faction`'s player gives way to a bot: in the game itself (recorded, so a reload does it again
        with the same bot) and in the game's seats. The player's connections in the game leave it if
        they no longer have a part in it."""
        record = games.get(self.store, game_id)
        loser = self._users_by_faction(record).get(faction)
        seed = random.SystemRandom().randrange(2 ** 31)
        out = self.sessions[game_id].handle_message(
            {'type': 'convert_to_bot', 'faction': faction, 'seed': seed, 'reason': reason})
        if any(m.get('type') == 'error' for m in out):
            raise StoreError([m['message'] for m in out if m.get('type') == 'error'])
        record = games.seat_to_bot(self.store, game_id, faction, reason)
        deliveries = self._route(game_id, None, out)
        if loser is not None and not self._takes_part(loser, record):
            for c in self.connections.values():
                if c.game_id == game_id and c.user and c.user['id'] == loser:
                    c.game_id = None
                    deliveries.append(([c.key], {'type': 'left_game', 'reason': SEAT_LOST.get(reason, reason)}))
        self._save(game_id)
        return deliveries

    _HANDLERS = {
        'scenarios': _scenarios, 'save_settings': _save_settings, 'reset_settings': _reset_settings,
        'save_scenario': _save_scenario, 'delete_scenario': _delete_scenario,
        'save_shared': _save_shared, 'delete_shared': _delete_shared,
        'create_game': _create_game, 'my_games': _my_games, 'browse': _browse, 'stop_browsing': _stop_browsing,
        'join_code': _join_code, 'enter_lobby': _enter_lobby, 'leave_lobby': _leave_lobby,
        'take_seat': _take_seat, 'leave_seat': _leave_seat, 'launch': _launch, 'cancel': _cancel, 'chat': _chat,
        'admin_overview': _admin_overview, 'admin_set_max_users': _admin_set_max_users, 'admin_find': _admin_find,
        'admin_lock': _admin_lock, 'hand_over': _hand_over, 'replace_with_bot': _replace_with_bot,
        'game_chat': _game_chat, 'game_chat_history': _game_chat_history,
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
        before = record.get('progress') or {}
        same_turn = before.get('turn') == gs.global_turn and before.get('active_faction') == gs.active_faction
        games.update_progress(self.store, game_id, {
            'round': gs.round_number, 'turn': gs.global_turn, 'active_faction': gs.active_faction,
            'waiting_for': sorted({users[f] for f in game.waiting_for() if f in users}),
            'turn_started': before.get('turn_started') if same_turn and before.get('turn_started') else utc_now()})
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



def _duration(seconds):
    """'3 hours 20 minutes' and the like, for a turn's time left."""
    minutes = int(seconds // 60) + (1 if seconds % 60 else 0)
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []) + \
        ([f"{minutes} minute{'s' if minutes != 1 else ''}"] if minutes or not hours else [])
    return ' '.join(parts)
