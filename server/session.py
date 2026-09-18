"""
GameSession: the server's per-game logic, deliberately split from
server/app.py's actual WebSocket I/O so it can be unit-tested the same
way engine/ is -- by calling methods directly and inspecting return
values, never opening a real socket (see server/tests/test_session.py).

Message protocol (first vertical slice -- Purchase phase only as a real
human decision; every other phase is auto-driven by the server: empty
orders for Combat Move/Non-Combat Move on a HUMAN's own turn -- no
legal-move UI for either yet -- or a real bot decision via engine.bots.
random_bot.RandomBot on a BOT faction's turn. Combat Resolution always
runs automatically (no player choice in HOW it resolves), but its
roll-by-roll narrative is always captured and sent for playback either
way -- see turn_log.TurnLog):

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
        every following automatic phase for THIS faction's own turn
        through to Alliances, broadcasting a "combat_events" message
        first if that drove any battles (so the human can watch their
        own attack land, using the same playback UI a bot's turn uses).
        Then: a broadcast "state"; if the faction that just finished was
        the last one before the game ends, "game_over"; otherwise
        advance_turn() runs and, for as long as the NEW active faction
        is BOT-controlled, its entire turn is played out immediately (a
        bot never waits for input) and shipped as one "bot_turn" message
        per bot turn (every order it made and every roll of every battle,
        in order) before the loop finally reaches a HUMAN faction's turn
        (a "your_turn") or the game ends.

Server -> client (each a dict; a "to": faction_code key means send only
to that faction's connection(s), no "to" key means broadcast to every
connection on this game -- everyone sees the same board, no fog of war):
    {"type": "state", "game_state": <GameState.to_dict()>}
    {"type": "your_turn", "faction": "NAA", "phase": "PURCHASE",
     "legal_purchase_targets": {"sc_targets": [...], "other_targets": [...]}}
    {"type": "purchases_staged", "to": "NAA", "total_cost": 12}
    {"type": "combat_events", "faction": "NAA", "events": [...]}
        Only the battle_event entries (turn_log.TurnLog.record_battle_
        events' shape) from a HUMAN faction's own Combat Resolution --
        not mixed with its purchase/move/capture events, which the human
        already knows (it just chose them, and "state"/its own earlier
        replies already cover them).
    {"type": "bot_turn", "faction": "AAC", "events": [...]}
        EVERY turn_log.TurnLog event from one bot's entire turn, in
        order -- purchase decided, combat moves decided, every battle's
        roll-by-roll events, non-combat moves, captures, deploys, income,
        alliance action. One message per bot turn (several may arrive in
        a row if multiple bots go before the next human's turn).
    {"type": "error", "to": "NAA", "message": "..."}
    {"type": "game_over"}

Not yet built (see docs/GAME_ARCHITECTURE.md): Combat Move/Non-Combat
Move/Alliances as real HUMAN decision points (submitted as empty orders
here -- fine for now since the one demo scenario, server.app._build_demo_
session, gives the human faction no reason to need them yet) and the
legal-move-query messages that would need; multiple simultaneous games
(one GameSession per server process for now); real auth/session
management (a "join" message is trusted at face value -- nothing stops
two connections both claiming the same faction); client-controlled
PACING of playback is entirely a client-side concern once it has a
"bot_turn"/"combat_events" message's full event list (decided this
session) -- the server never paces delivery itself.
"""
from engine.engine import PurchaseOrder
from engine.state import FactionMode, Phase


class GameSession:
    def __init__(self, engine, turn_log, bots=None):
        self.engine = engine
        self.turn_log = turn_log
        self.bots = bots or {}  # faction_code -> RandomBot, one per BOT faction in play

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

        start = len(self.turn_log.events)
        self._drain_phases(faction, bot=None)
        combat_events = [e for e in self.turn_log.events[start:] if e['kind'] == 'battle_event']

        messages = []
        if combat_events:
            messages.append({'type': 'combat_events', 'faction': faction, 'events': combat_events})
        messages.append(self._state_message())
        if gs.game_over:
            messages.append({'type': 'game_over'})
            return messages
        self.engine.advance_turn()
        return messages + self._play_bot_turns_until_human_or_game_over()

    def _drain_phases(self, faction, bot):
        """Runs `faction`'s turn from wherever GameState.phase currently
        is through to the end of the Alliances phase (everything up to
        but not including advance_turn). `bot`, if given, makes its own
        Purchase/Combat Move/Non-Combat Move/Alliance decisions (a full
        bot turn, since GameState.phase starts back at PURCHASE right
        after advance_turn -- see _play_bot_turns_until_human_or_game_
        over); `bot=None` means those phases are left exactly as the
        caller already handled them (Purchase, by the human's own
        confirm_purchases, called just before this) or auto-submitted
        empty (Combat Move/Non-Combat Move -- see this module's own
        docstring on what's not built yet). Combat Resolution always
        runs for real either way -- there's no player choice in HOW it
        resolves, only in whether to attack at all (Combat Move).
        Mirrors engine.bots.driver.play_to_completion's single-while-loop
        shape deliberately -- see that driver's own docstring for why the
        equivalent-looking fixed-block-per-phase shape is a real bug,
        found and fixed earlier this project: call advance_phase()
        exactly once per phase actually encountered, never once per phase
        in a fixed row, since a disabled first-turn phase (game_start_
        settings) means GameState.phase may skip a step entirely."""
        gs = self.engine.game_state
        while gs.phase != Phase.ALLIANCES:
            if gs.phase == Phase.PURCHASE:
                if bot is not None:
                    bot.take_purchase_phase()
            elif gs.phase == Phase.COMBAT_MOVE:
                if bot is not None:
                    bot.take_combat_move_phase()
                else:
                    self.engine.submit_combat_moves(faction, [])
                    self.engine.confirm_combat_moves(faction)
            elif gs.phase == Phase.COMBAT_RESOLUTION:
                self.engine.resolve_combat(faction)
            elif gs.phase == Phase.NONCOMBAT_MOVE:
                if bot is not None:
                    # RandomBot.take_noncombat_move_phase() already calls
                    # process_return_to_base itself, first thing -- must
                    # NOT also be called here too (a second call raises,
                    # "already processed return-to-base this turn").
                    bot.take_noncombat_move_phase()
                else:
                    self.engine.process_return_to_base(faction)
                    self.engine.submit_noncombat_moves(faction, [])
                    self.engine.confirm_noncombat_moves(faction)
            elif gs.phase == Phase.CAPTURE:
                self.engine.process_capture_territory(faction)
                self.engine.process_elimination_check()
            elif gs.phase == Phase.DEPLOY_INCOME:
                self.engine.deploy_and_collect_income(faction)
            self.engine.advance_phase()

        if bot is not None:
            bot.take_alliance_phase()
        self.engine.process_game_end_check(faction)

    def _play_bot_turns_until_human_or_game_over(self):
        """Called right after advance_turn() -- plays out every
        consecutive BOT faction's entire turn (a bot never waits for
        input) until either a HUMAN faction's turn comes up or the game
        ends. Returns one "bot_turn" message (the WHOLE turn's worth of
        turn_log events, in order -- unlike _handle_confirm_purchases'
        "combat_events", which deliberately narrows to just the battle
        narrative since a human already knows its OWN other decisions)
        plus a "state" broadcast per bot turn played, then a final
        "your_turn" once a human's turn is reached, or "game_over"."""
        gs = self.engine.game_state
        messages = []
        while gs.active_faction is not None and gs.factions[gs.active_faction].mode == FactionMode.BOT:
            faction = gs.active_faction
            start = len(self.turn_log.events)
            self._drain_phases(faction, bot=self.bots[faction])
            messages.append({'type': 'bot_turn', 'faction': faction, 'events': self.turn_log.events[start:]})
            messages.append(self._state_message())
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
        sc_targets, other_targets = self.engine.legal_purchase_targets(gs.active_faction)
        return {
            'type': 'your_turn', 'faction': gs.active_faction, 'phase': gs.phase.value,
            'legal_purchase_targets': {'sc_targets': sc_targets, 'other_targets': other_targets},
        }

    @staticmethod
    def _error(faction, message):
        return {'type': 'error', 'to': faction, 'message': message}
