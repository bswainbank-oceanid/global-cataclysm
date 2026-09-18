"""
GameSession: the server's per-game logic, deliberately split from
server/app.py's actual WebSocket I/O so it can be unit-tested the same
way engine/ is -- by calling methods directly and inspecting return
values, never opening a real socket (see server/tests/test_session.py).

Message protocol (Purchase, Combat Move, and Non-Combat Move are all real
human decisions now, same one-shot shape each; Alliances is still a no-op
for a HUMAN's own turn -- no decision UI for it yet -- or a real bot
decision via engine.bots.random_bot.RandomBot on a BOT faction's turn.
Combat Resolution always runs automatically (no player choice in HOW it
resolves), but its roll-by-roll narrative is always captured and sent for
playback either way -- see turn_log.TurnLog):

Client -> server (each a dict with at least "type" and "faction"):
    {"type": "join", "faction": "NAA"}
        Associates this connection with a faction (no real auth yet --
        see this module's own docstring note on that). Always answered
        with a full "state" message; also a "your_turn" if it's already
        this faction's turn AND currently at a phase needing a decision
        (Purchase, Combat Move, or Non-Combat Move) -- e.g. a client
        reconnecting mid-turn.
    {"type": "purchase", "faction": "NAA",
     "orders": [{"unit_type": "Infantry", "qty": 2, "deploy_at": 5}, ...]}
        The COMPLETE, final order list for this Purchase phase, one shot
        -- decided this session: the client owns territory selection and
        MPC budget tracking itself (it already needs its own copy of
        units.json for cost/type info to render the purchase UI at all),
        so there's nothing left for a separate stage-then-confirm round
        trip to teach the client that it doesn't already know. Calls
        engine.submit_purchases then confirm_purchases back to back
        (still two engine-level calls, just not two client-facing
        messages -- a failed submit_purchases never partially applies
        anything, so there's nothing to unwind if it's rejected); an
        "error" if either raises, otherwise continues the turn (see
        _continue_human_turn) -- usually straight into a Combat Move
        "your_turn", since that's always the very next phase.
    {"type": "combat_move", "faction": "NAA",
     "orders": [{"unit_id": 42, "path": [5, 6]}, ...]}
        The COMPLETE, final combat-move order list, one shot, same
        reasoning as "purchase" -- decided this session: "the legal
        combat move options for each unit is known at turn start [see
        GameEngine.legal_combat_move_options, sent in the preceding
        "your_turn"]. The client can pick from those options and send to
        server." Calls submit_combat_moves then confirm_combat_moves back
        to back; an "error" if either raises (including simply being the
        wrong phase -- both engine calls already check that themselves),
        otherwise continues the turn: drains every following automatic
        phase through to Non-Combat Move, broadcasting a "combat_events"
        message first if Combat Resolution drove any battles (so the
        human can watch their own attack land, using the same playback UI
        a bot's turn uses) -- then a Non-Combat Move "your_turn".
    {"type": "noncombat_move", "faction": "NAA",
     "orders": [{"unit_id": 7, "destination": 12}, ...]}
        The COMPLETE, final non-combat-move order list, one shot, same
        reasoning again -- decided this session: "non-combat move options
        can change after combat[, so the] similar pattern[: get] the full
        list of legal options after combat[; the] user submits their
        choices" [see GameEngine.legal_noncombat_move_options, sent in the
        preceding "your_turn" -- computed only once this phase is actually
        reached, since what's legal genuinely depends on how Combat
        Resolution just played out, unlike Purchase/Combat Move's turn-
        start snapshots]. process_return_to_base (every surviving air unit
        that combat-moved snapping back to where it took off from) always
        runs automatically the moment this phase is reached, BEFORE the
        "your_turn" prompt is even sent -- not part of this message, and
        not something the client orders. Calls submit_noncombat_moves then
        confirm_noncombat_moves back to back; an "error" if either raises,
        otherwise continues the turn same as "combat_move" -- drains
        through to Alliances (no narration to add here: unlike Combat
        Resolution, a non-combat move has no roll-by-roll of its own) --
        then a broadcast "state"; game over, or advance_turn() and
        however many bot turns follow before the next human's turn.
        Deliberately permissive about stranding: an order that leaves an
        Air unit sitting over open water with no own carrier is legal to
        submit -- the engine only enforces the actual loss at phase end
        (movement.stranded_aircraft_rule), same as it always has. Warning
        the player beforehand is entirely the client's job -- decided this
        session: "the client can handle warning the player about stranding
        aircraft[; the] server can allow that as a legal move."

Server -> client (each a dict; a "to": faction_code key means send only
to that faction's connection(s), no "to" key means broadcast to every
connection on this game -- everyone sees the same board, no fog of war):
    {"type": "state", "game_state": <GameState.to_dict()>}
    {"type": "your_turn", "faction": "NAA", "phase": "PURCHASE",
     "legal_purchase_targets": {"sc_targets": [...], "other_targets": [...]}}
    {"type": "your_turn", "faction": "NAA", "phase": "COMBAT_MOVE",
     "legal_combat_moves": {unit_id: {"unit_type", "territory_id",
     "destinations": {dest_id: path}}, ...}}
    {"type": "your_turn", "faction": "NAA", "phase": "NONCOMBAT_MOVE",
     "legal_noncombat_moves": {unit_id: {"unit_type", "territory_id",
     "destinations": [dest_id, ...]}, ...}}
        Same "your_turn" type every time -- it's still this faction's own
        turn, just a later phase of it -- the client tells them apart by
        "phase" and reads whichever legal_* key matches (see
        GameEngine.legal_purchase_targets/legal_combat_move_options/
        legal_noncombat_move_options for exactly what those contain --
        note legal_noncombat_moves' destinations are plain ids, not
        {destination: path}, since NonCombatMoveOrder only needs the
        endpoint).
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

Not yet built (see docs/GAME_ARCHITECTURE.md): Alliances as a real HUMAN
decision point (a no-op here for a HUMAN's own turn -- fine for now since
the one demo scenario, server.app._build_demo_session, gives the human
faction no reason to need it yet). When it is built, expect the same
one-shot shape the other phases use: the client composes the complete
decision itself and sends it once -- not a per-step or stage-then-confirm
round trip. Also not yet built: multiple simultaneous games (one
GameSession per server process for now); real auth/session management (a
"join" message is trusted at face value -- nothing stops two connections
both claiming the same faction); client-controlled PACING of playback is
entirely a client-side concern once it has a "bot_turn"/"combat_events"
message's full event list (decided this session) -- the server never
paces delivery itself.
"""
from engine.engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from engine.state import FactionMode, Phase

# Phases where GameState.active_faction (a HUMAN one) is waiting on this
# module for a real decision -- see connect()/_decision_prompt.
_HUMAN_DECISION_PHASES = (Phase.PURCHASE, Phase.COMBAT_MOVE, Phase.NONCOMBAT_MOVE)


class GameSession:
    def __init__(self, engine, turn_log, bots=None):
        self.engine = engine
        self.turn_log = turn_log
        self.bots = bots or {}  # faction_code -> RandomBot, one per BOT faction in play

    def connect(self, faction):
        """A client just identified itself as `faction` ("join"). Pure
        query/no mutation -- returns the messages to send it (always the
        current state; a "your_turn" too if it's already this faction's
        turn at a phase needing a decision -- e.g. reconnecting mid-turn,
        not just a fresh Purchase phase). Safe to call even mid-Non-Combat
        Move: process_return_to_base already ran (see _drain_phases)
        before this faction's phase could ever be NONCOMBAT_MOVE with
        active_faction pointed at it, so _decision_prompt's legal_
        noncombat_move_options call below reflects that, not a stale
        pre-return-to-base snapshot."""
        gs = self.engine.game_state
        if faction not in gs.factions:
            return [self._error(faction, f'no such faction: {faction}')]
        if gs.factions[faction].mode != FactionMode.HUMAN:
            return [self._error(faction, f'{faction} is not a HUMAN-controlled faction')]

        messages = [self._state_message()]
        if faction == gs.active_faction and gs.phase in _HUMAN_DECISION_PHASES:
            messages.append(self._decision_prompt(faction))
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
        if msg_type == 'purchase':
            return self._handle_purchase(faction, msg.get('orders') or [])
        if msg_type == 'combat_move':
            return self._handle_combat_move(faction, msg.get('orders') or [])
        if msg_type == 'noncombat_move':
            return self._handle_noncombat_move(faction, msg.get('orders') or [])
        return [self._error(faction, f'unknown message type: {msg_type!r}')]

    def _handle_purchase(self, faction, raw_orders):
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        try:
            orders = [PurchaseOrder(o['unit_type'], o['qty'], o['deploy_at']) for o in raw_orders]
        except (KeyError, TypeError) as e:
            return [self._error(faction, f'malformed order: {e}')]

        try:
            self.engine.submit_purchases(faction, orders)
            self.engine.confirm_purchases(faction)
        except ValueError as e:
            return [self._error(faction, str(e))]
        return self._continue_human_turn(faction)

    def _handle_combat_move(self, faction, raw_orders):
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        try:
            orders = [CombatMoveOrder(o['unit_id'], list(o['path'])) for o in raw_orders]
        except (KeyError, TypeError) as e:
            return [self._error(faction, f'malformed order: {e}')]

        try:
            self.engine.submit_combat_moves(faction, orders)
            self.engine.confirm_combat_moves(faction)
        except ValueError as e:
            return [self._error(faction, str(e))]
        # Confirming doesn't itself move GameState.phase off Combat Move
        # (same as Purchase) -- but unlike Purchase, _drain_phases below
        # would immediately stop again right where it started if phase
        # were still COMBAT_MOVE when it's entered (that's exactly the
        # condition it stops on) -- so this one has to actually advance
        # past it first, itself, before resuming the drain.
        self.engine.advance_phase()
        return self._continue_human_turn(faction)

    def _handle_noncombat_move(self, faction, raw_orders):
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        try:
            orders = [NonCombatMoveOrder(o['unit_id'], o['destination']) for o in raw_orders]
        except (KeyError, TypeError) as e:
            return [self._error(faction, f'malformed order: {e}')]

        try:
            self.engine.submit_noncombat_moves(faction, orders)
            self.engine.confirm_noncombat_moves(faction)
        except ValueError as e:
            return [self._error(faction, str(e))]
        # Same reason as _handle_combat_move: confirming doesn't itself
        # move GameState.phase off Non-Combat Move, and _drain_phases
        # would immediately stop again right where it started otherwise.
        self.engine.advance_phase()
        return self._continue_human_turn(faction)

    def _continue_human_turn(self, faction):
        """Drains as much of `faction`'s own turn as possible (bot=None),
        starting from wherever GameState.phase currently is. Stops at
        Combat Move or Non-Combat Move -- returning that phase's
        "your_turn" prompt, awaiting that decision -- or all the way
        through to the end of Alliances: the turn's fully done, so this
        reports the resulting state, game-over, or hands off to
        advance_turn() and however many bot turns follow before the next
        human's turn. Either way, any battle_event entries logged during
        THIS call (i.e. from Combat Resolution, if Combat Move is what
        was just confirmed) are reported as "combat_events" first, even
        when the drain stops again right after at Non-Combat Move --
        a human's own attack landing must never go unreported just
        because Non-Combat Move is now also an interrupt point; it can
        no longer be assumed, as it could when only Combat Move stopped
        the drain, that "not finished" means "nothing happened yet"."""
        gs = self.engine.game_state
        start = len(self.turn_log.events)
        finished = self._drain_phases(faction, bot=None)

        combat_events = [e for e in self.turn_log.events[start:] if e['kind'] == 'battle_event']
        messages = []
        if combat_events:
            messages.append({'type': 'combat_events', 'faction': faction, 'events': combat_events})
        if not finished:
            messages.append(self._decision_prompt(faction))
            return messages

        messages.append(self._state_message())
        if gs.game_over:
            messages.append({'type': 'game_over'})
            return messages
        self.engine.advance_turn()
        return messages + self._play_bot_turns_until_human_or_game_over()

    def _drain_phases(self, faction, bot):
        """Runs `faction`'s turn from wherever GameState.phase currently
        is through to the end of the Alliances phase (everything up to
        but not including advance_turn) -- UNLESS it reaches Combat Move
        or Non-Combat Move with `bot=None` (a human awaiting that
        decision), in which case it stops right there and returns False
        without touching that phase's actual decision at all; the caller
        is responsible for prompting and, once a decision arrives,
        advancing off the phase itself before calling back in here to
        resume (see _handle_combat_move/_handle_noncombat_move). Returns
        True if it ran all the way through to Alliances.

        `bot`, if given, makes its own Purchase/Combat Move/Non-Combat
        Move/Alliance decisions (a full bot turn, since GameState.phase
        starts back at PURCHASE right after advance_turn -- see
        _play_bot_turns_until_human_or_game_over) -- for a bot, neither
        Combat Move nor Non-Combat Move ever triggers the stop-and-
        return-False above, since a bot never waits for input. `bot=None`
        means Purchase/Combat Move/Non-Combat Move are each left exactly
        as the caller already handled them just before calling this (see
        those _handle_* methods). Combat Resolution always runs for real
        either way -- there's no player choice in HOW it resolves, only
        in whether to attack at all (Combat Move). Mirrors
        engine.bots.driver.play_to_completion's single-while-loop shape
        deliberately -- see that driver's own docstring for why the
        equivalent-looking fixed-block-per-phase shape is a real bug,
        found and fixed earlier this project: call advance_phase()
        exactly once per phase actually encountered, never once per phase
        in a fixed row, since a disabled first-turn phase (game_start_
        settings) means GameState.phase may skip a step entirely."""
        gs = self.engine.game_state
        while gs.phase != Phase.ALLIANCES:
            if gs.phase == Phase.COMBAT_MOVE and bot is None:
                return False
            if gs.phase == Phase.PURCHASE:
                if bot is not None:
                    bot.take_purchase_phase()
            elif gs.phase == Phase.COMBAT_MOVE:
                bot.take_combat_move_phase()
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
                    # Always automatic, even for a human -- it's not a
                    # decision, just bookkeeping that has to happen
                    # before legal_noncombat_move_options (sent by the
                    # "your_turn" prompt right below) is even meaningful
                    # to compute. Then stop and wait for the real
                    # decision, same shape as Combat Move above.
                    self.engine.process_return_to_base(faction)
                    return False
            elif gs.phase == Phase.CAPTURE:
                self.engine.process_capture_territory(faction)
                self.engine.process_elimination_check()
            elif gs.phase == Phase.DEPLOY_INCOME:
                self.engine.deploy_and_collect_income(faction)
            self.engine.advance_phase()

        if bot is not None:
            bot.take_alliance_phase()
        self.engine.process_game_end_check(faction)
        return True

    def _play_bot_turns_until_human_or_game_over(self):
        """Called right after advance_turn() -- plays out every
        consecutive BOT faction's entire turn (a bot never waits for
        input) until either a HUMAN faction's turn comes up or the game
        ends. Returns one "bot_turn" message (the WHOLE turn's worth of
        turn_log events, in order -- unlike _continue_human_turn's
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
        messages.append(self._decision_prompt(gs.active_faction))
        return messages

    def _state_message(self):
        return {'type': 'state', 'game_state': self.engine.game_state.to_dict()}

    def _decision_prompt(self, faction):
        """The "your_turn" message prompting `faction` for whatever
        decision is next -- a fresh Purchase phase, or a Combat Move/
        Non-Combat Move decision later the same turn. Always type
        "your_turn" either way (it's still this faction's own turn); the
        client tells them apart by "phase" and reads whichever legal_*
        key is present. For NONCOMBAT_MOVE specifically, this is only
        ever called after _drain_phases has already run process_return_
        to_base for this faction this turn (see there) -- so legal_
        noncombat_move_options genuinely reflects what's left to decide,
        not units that already got automatically sent back to base."""
        gs = self.engine.game_state
        msg = {'type': 'your_turn', 'faction': faction, 'phase': gs.phase.value}
        if gs.phase == Phase.PURCHASE:
            sc_targets, other_targets = self.engine.legal_purchase_targets(faction)
            msg['legal_purchase_targets'] = {'sc_targets': sc_targets, 'other_targets': other_targets}
        elif gs.phase == Phase.COMBAT_MOVE:
            msg['legal_combat_moves'] = self.engine.legal_combat_move_options(faction)
        elif gs.phase == Phase.NONCOMBAT_MOVE:
            msg['legal_noncombat_moves'] = self.engine.legal_noncombat_move_options(faction)
        return msg

    @staticmethod
    def _error(faction, message):
        return {'type': 'error', 'to': faction, 'message': message}
