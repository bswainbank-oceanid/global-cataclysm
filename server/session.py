"""
GameSession: the server's per-game logic, deliberately split from
server/app.py's actual WebSocket I/O so it can be unit-tested the same
way engine/ is -- by calling methods directly and inspecting return
values, never opening a real socket (see server/tests/test_session.py).

Message protocol (first vertical slice -- Purchase phase only; every
other phase is auto-driven by the server exactly the way engine.bots.
driver auto-drives a bot's non-decision phases, see _advance_automatic_
phases):

Client -> server (each a dict with at least "type" and "faction"):
    {"type": "join", "faction": "NAA"}
        Associates this connection with a faction (no real auth yet --
        see this module's own docstring note on that). Always answered
        with a full "state" message; also a "your_turn" if it's already
        this faction's Purchase phase.
    {"type": "submit_purchases", "faction": "NAA",
     "orders": [{"unit_type": "Infantry", "qty": 2, "deploy_at": 5}, ...]}
        Stages (not yet committed -- engine.engine.GameEngine.
        submit_purchases semantics) a purchase order list, validated
        against the real engine. Answered with "purchases_staged" (and
        the running total cost) or "error", sent only to the faction
        that submitted it -- staging isn't visible in GameState yet, so
        there's nothing to broadcast.
    {"type": "confirm_purchases", "faction": "NAA"}
        Commits the staged list (engine.confirm_purchases), then drains
        every following automatic phase through to the next decision
        point or the end of the game. Answered with a broadcast "state",
        then either "your_turn" (possibly for a DIFFERENT faction, once
        advance_turn runs) or "game_over".

Server -> client (each a dict; a "to": faction_code key means send only
to that faction's connection(s), no "to" key means broadcast to every
connection on this game -- everyone sees the same board, no fog of war):
    {"type": "state", "game_state": <GameState.to_dict()>}
    {"type": "your_turn", "faction": "NAA", "phase": "PURCHASE"}
    {"type": "purchases_staged", "to": "NAA", "total_cost": 12}
    {"type": "error", "to": "NAA", "message": "..."}
    {"type": "game_over"}

Not yet built (see docs/GAME_ARCHITECTURE.md): Combat Move/Non-Combat
Move/Alliances as real client decision points (submitted as empty orders
here -- fine for now since the one demo scenario, server.app._build_demo_
session, has no enemies to fight or allies to make); multiple
simultaneous games (one GameSession per server process for now); real
auth/session management (a "join" is trusted at face value -- nothing
stops two connections both claiming the same faction, or a faction
nobody claimed never getting its turn played); legal-move query
messages for the UI to highlight valid options before submitting.
"""
from engine.engine import PurchaseOrder
from engine.state import FactionMode, Phase


class GameSession:
    def __init__(self, engine):
        self.engine = engine

    def connect(self, faction):
        """A client just identified itself as `faction` ("join"). Pure
        query/no mutation -- returns the messages to send it (always the
        current state; a "your_turn" too if it's already this faction's
        Purchase phase)."""
        gs = self.engine.game_state
        if faction not in gs.factions:
            return [self._error(faction, f'no such faction: {faction}')]
        if gs.factions[faction].mode != FactionMode.HUMAN:
            return [self._error(faction, f'{faction} is not a HUMAN-controlled faction')]

        messages = [self._state_message()]
        if faction == gs.active_faction and gs.phase == Phase.PURCHASE:
            messages.append(self._your_turn_message())
        return messages

    def handle_message(self, msg):
        """Dispatches one already-JSON-decoded client message. Returns
        the list of messages (dicts, JSON-ready) the caller (server.app,
        which owns the actual sockets) should deliver -- each routed by
        its own "to" key if present, else broadcast. Never raises for a
        malformed/unknown message; that becomes an "error" reply instead,
        same as any other rejected request."""
        msg_type = msg.get('type')
        faction = msg.get('faction')
        if msg_type == 'join':
            return self.connect(faction)
        if msg_type == 'submit_purchases':
            return self._handle_submit_purchases(faction, msg.get('orders') or [])
        if msg_type == 'confirm_purchases':
            return self._handle_confirm_purchases(faction)
        return [self._error(faction, f'unknown message type: {msg_type!r}')]

    def _handle_submit_purchases(self, faction, raw_orders):
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        try:
            orders = [PurchaseOrder(o['unit_type'], o['qty'], o['deploy_at']) for o in raw_orders]
        except (KeyError, TypeError) as e:
            return [self._error(faction, f'malformed order: {e}')]

        try:
            total_cost = self.engine.submit_purchases(faction, orders)
        except ValueError as e:
            return [self._error(faction, str(e))]
        return [{'type': 'purchases_staged', 'to': faction, 'total_cost': total_cost}]

    def _handle_confirm_purchases(self, faction):
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        try:
            self.engine.confirm_purchases(faction)
        except ValueError as e:
            return [self._error(faction, str(e))]
        return self._advance_automatic_phases(faction)

    def _advance_automatic_phases(self, faction):
        """Runs everything between `faction`'s just-confirmed Purchase
        phase and the next real decision point (or the end of the game)
        -- Combat Move/Non-Combat Move submit empty order lists (no real
        UI for either yet, see this module's docstring), everything else
        is the same automatic, no-player-choice call engine.bots.driver
        makes for a bot. Mirrors that driver's single-while-loop shape
        deliberately (see its own docstring for why the equivalent-
        looking fixed-block-per-phase shape is a real bug, found and
        fixed earlier this project): call advance_phase() exactly once
        per phase actually encountered, never once per phase in a fixed
        row, since a disabled first-turn phase (game_start_settings)
        means GameState.phase may skip a step entirely."""
        gs = self.engine.game_state
        self.engine.advance_phase()  # off Purchase, just confirmed by the caller
        while gs.phase != Phase.ALLIANCES:
            if gs.phase == Phase.COMBAT_MOVE:
                self.engine.submit_combat_moves(faction, [])
                self.engine.confirm_combat_moves(faction)
            elif gs.phase == Phase.COMBAT_RESOLUTION:
                self.engine.resolve_combat(faction)
            elif gs.phase == Phase.NONCOMBAT_MOVE:
                self.engine.process_return_to_base(faction)
                self.engine.submit_noncombat_moves(faction, [])
                self.engine.confirm_noncombat_moves(faction)
            elif gs.phase == Phase.CAPTURE:
                self.engine.process_capture_territory(faction)
                self.engine.process_elimination_check()
            elif gs.phase == Phase.DEPLOY_INCOME:
                self.engine.deploy_and_collect_income(faction)
            self.engine.advance_phase()

        # Alliances: no real decision point yet either (see docstring) --
        # every faction in the one demo scenario is NEUTRAL except the
        # single human, so there's nobody to invite/withdraw from anyway.
        self.engine.process_game_end_check(faction)

        messages = [self._state_message()]
        if gs.game_over:
            messages.append({'type': 'game_over'})
            return messages
        self.engine.advance_turn()
        messages.append(self._your_turn_message())
        return messages

    def _state_message(self):
        return {'type': 'state', 'game_state': self.engine.game_state.to_dict()}

    def _your_turn_message(self):
        gs = self.engine.game_state
        return {'type': 'your_turn', 'faction': gs.active_faction, 'phase': gs.phase.value}

    @staticmethod
    def _error(faction, message):
        return {'type': 'error', 'to': faction, 'message': message}
