"""
GameHost: what the server holds between the launch screen and the game -- the
current GameSession (or none yet) and the launch-screen protocol around it. Like
GameSession it never touches a socket; server/app.py delivers what it returns.

Client -> server (besides the messages GameSession/PhaseStepper handle):
    {"type": "lobby"}                       ask what the launch screen should offer
    {"type": "new_game", "settings": {...}} build a game from launch settings
                                            (server/lobby.py), replacing any
                                            running one; the sender becomes a watcher
    {"type": "save_setup", "setup": {id, name, description, settings}}
                                            save a scenario setup (server/setups.py):
                                            the same name updates setup `id`, a new
                                            name makes a new one
    {"type": "delete_setup", "id": ...}     delete a saved scenario setup

Server -> client:
    {"type": "lobby", "game_running": bool, "seats": [...], "new_scenario": {...},
     "setups": [{id, name, description, settings}, ...]}    sent to every new connection
    {"type": "setups", "setups": [...], "selected": id | null}
        the saved setups after a save (selected: the one saved) or a delete (null)
    {"type": "game_started", "seats": [{seat, faction, mode, strategy, behavior}, ...]}
        the new game exists; followed by the usual "state" and "phase_queue"
    {"type": "error", "message": ..., "problems": [...]}   settings rejected
"""
from engine.repository import default_repository
from . import setups
from .lobby import LobbyError, build_session, generator_info


class GameHost:
    def __init__(self, session=None, seats=None, repo=None):
        self.session = session
        self.seats = seats or []
        self.repo = repo or default_repository()  # where scenario setups are saved

    def lobby_message(self):
        return {'type': 'lobby', 'game_running': self.session is not None,
                'seats': self.seats if self.session is not None else [],
                'new_scenario': generator_info(), 'setups': setups.listing(self.repo)}

    def handle(self, msg):
        """(direct, broadcast, joins_as_watcher): messages for the sender alone,
        messages for the game's watchers / their own 'to' targets, and whether the
        sender should now be registered as a watcher."""
        kind = msg.get('type')
        if kind == 'lobby':
            return [self.lobby_message()], [], False
        if kind in ('save_setup', 'delete_setup'):
            try:
                if kind == 'save_setup':
                    selected = setups.save(msg.get('setup') or {}, self.repo)
                else:
                    setups.delete(msg.get('id'), self.repo)
                    selected = None
            except LobbyError as e:
                return [{'type': 'error', 'message': str(e), 'problems': e.problems}], [], False
            return [{'type': 'setups', 'setups': setups.listing(self.repo), 'selected': selected}], [], False
        if kind == 'new_game':
            try:
                session, seats = build_session(msg.get('settings') or {})
            except LobbyError as e:
                return [{'type': 'error', 'message': str(e), 'problems': e.problems}], [], False
            self.session, self.seats = session, seats
            started = {'type': 'game_started', 'seats': seats}
            return [], [started] + session.handle_message({'type': 'watch'}), True
        if self.session is None:
            return [{'type': 'error', 'message': 'no game is running: start one from the launch screen'}], [], False
        return [], self.session.handle_message(msg), False
