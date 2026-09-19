"""
GameSession: the server's per-game logic, deliberately split from
server/app.py's actual WebSocket I/O so it can be unit-tested the same
way engine/ is -- by calling methods directly and inspecting return
values, never opening a real socket (see server/tests/test_session.py).

Message protocol (Purchase, Combat Move, Non-Combat Move, and Alliances
are all real human decisions now, same one-shot shape each -- or a real
bot decision via engine.bots.random_bot.RandomBot on a BOT faction's
turn. Combat Resolution always runs automatically (no player choice in
HOW it resolves), but its roll-by-roll narrative is always captured and
sent for playback either way -- see turn_log.TurnLog):

Client -> server (each a dict with at least "type" and "faction"):
    {"type": "join", "faction": "NAA"}
        Associates this connection with a faction (no real auth yet --
        see this module's own docstring note on that). Always answered
        with a full "state" message; also a "your_turn" if it's already
        this faction's turn AND currently at a phase needing a decision
        (Purchase, Combat Move, Non-Combat Move, or Alliances) -- e.g. a
        client reconnecting mid-turn.
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
        then an Alliances "your_turn". Deliberately permissive about
        stranding: an order that leaves an Air unit sitting over open
        water with no own carrier is legal to submit -- the engine only
        enforces the actual loss at phase end (movement.stranded_
        aircraft_rule), same as it always has. Warning the player
        beforehand is entirely the client's job -- decided this session:
        "the client can handle warning the player about stranding
        aircraft[; the] server can allow that as a legal move."
    {"type": "alliance_action", "faction": "NAA", "action": "none"}
    {"type": "alliance_action", "faction": "NAA", "action": "invite", "target": "AAC"}
    {"type": "alliance_action", "faction": "NAA", "action": "withdraw"}
        `faction`'s one optional action this turn (this session: "give
        the player the full set of legal options[; they] submit their
        instructions, which might be do nothing (an option for any
        phase)" -- unlike Purchase/Combat Move/Non-Combat Move, where
        "do nothing" is simply an empty `orders` list, Alliances has no
        list to leave empty, so "none" is its own explicit action value
        instead). "invite" additionally requires "target" (one of the
        preceding "your_turn"'s legal_alliance_options.eligible_invite_
        targets). Against a BOT target, the accept/decline is resolved
        immediately, synchronously, server-side via engine.bots.
        alliance_policy.accepts_invite -- exactly like a bot inviting
        another bot, since that policy function only ever reads the
        TARGET's own alliance_strategy, not the inviter's. Against a
        HUMAN target, there's genuinely nobody to synchronously ask --
        this instead starts the ONE out-of-turn exchange in the whole
        protocol (see "alliance_invite"/"alliance_invite_response"
        below): `faction`'s turn stays open, unfinished, until the
        target answers, however long that takes; a second
        "alliance_action" from `faction` in the meantime is rejected.
        For "none"/"withdraw", or "invite" against a BOT target, this
        calls invite_to_alliance/withdraw_from_alliance/neither, then
        process_game_end_check (the same call GameEngine._drain_phases'
        bot path always made here); an "error" if any of those raise
        (including simply being the wrong phase, since even "none" still
        calls process_game_end_check, which validates it), otherwise the
        turn is fully done -- same finishing sequence every other
        phase's confirm reaches once nothing is left to decide: a
        broadcast "state"; game over, or advance_turn() and however many
        bot turns follow before the next human's turn.
    {"type": "alliance_invite_response", "faction": "UE", "accept": true}
        `faction` answering a pending out-of-turn "alliance_invite" (see
        below) -- the ONE message in this whole protocol a client sends
        on a faction that is NOT GameState.active_faction; it's still
        the INVITER's turn, paused awaiting exactly this. "accept" is
        required (true or false; a decline is not an error -- it still
        resolves the invite and spends the inviter's one action for the
        turn, same as invite_to_alliance has always worked for a BOT
        decline). "error" if no invite is currently pending for
        `faction`, or if this races a phase-changing message somehow
        arriving first (defensive only -- nothing else CAN act while an
        invite is pending, since GameState.active_faction/phase never
        move until this resolves). Otherwise resolves invite_to_alliance
        with `faction`'s answer and finishes the INVITER's turn (not
        `faction`'s own -- it was never `faction`'s turn to begin with).

Watch mode (every faction a BOT, a spectator steps the game one phase at a
time): "watch" / "next" and the "phase_queue" / "phase_result" messages are
documented in server/stepper.py.

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
    {"type": "your_turn", "faction": "NAA", "phase": "ALLIANCES",
     "legal_alliance_options": {"eligible_invite_targets": [faction_code, ...],
     "can_withdraw": bool}}
        Same "your_turn" type every time -- it's still this faction's own
        turn, just a later phase of it -- the client tells them apart by
        "phase" and reads whichever legal_* key matches (see
        GameEngine.legal_purchase_targets/legal_combat_move_options/
        legal_noncombat_move_options/legal_alliance_options for exactly
        what those contain -- note legal_noncombat_moves' destinations
        are plain ids, not {destination: path}, since NonCombatMoveOrder
        only needs the endpoint; legal_alliance_options is a per-faction
        decision, not per-unit, so it has no per-unit map at all).
    {"type": "alliance_invite_sent", "to": "NAA", "target": "UE"}
        Acknowledges `faction`'s own "invite" against a HUMAN target was
        received and is now the pending out-of-turn exchange -- NOT a
        fresh ALLIANCES "your_turn" (nothing else can be decided until
        this resolves). Also what a reconnecting inviter gets instead of
        a "your_turn" (see connect()/_pending_invite_message).
    {"type": "alliance_invite", "to": "UE", "from": "NAA"}
        The out-of-turn prompt itself, sent to the invited HUMAN target
        -- answer with "alliance_invite_response". Also what a
        reconnecting target gets if it hasn't answered yet.
    {"type": "combat_events", "faction": "NAA", "events": [...]}
        Only the battle_event entries (turn_log.TurnLog.record_battle_
        events' shape) plus one battle_summary per battle (both sides'
        participants and casualties) from a HUMAN faction's own Combat
        Resolution -- not mixed with its purchase/move events, which the
        human already knows (it just chose them).
    {"type": "turn_events", "faction": "NAA", "events": [...]}
        The human's own Capture Territory / Deploy + Income results
        (territory_captured, unit_deployed, income_collected,
        faction_eliminated) -- phases the server drains automatically, so
        without this a client would only see their effects as a changed
        "state" at the end of the turn. Sent, when non-empty, right before
        whatever prompt follows.
    {"type": "bot_turn", "faction": "AAC", "events": [...]}
        EVERY turn_log.TurnLog event from one bot's entire turn, in
        order -- purchase decided, combat moves decided, every battle's
        roll-by-roll events, non-combat moves, captures, deploys, income,
        alliance action. One message per bot turn (several may arrive in
        a row if multiple bots go before the next human's turn).
    {"type": "error", "to": "NAA", "message": "..."}
    {"type": "game_over"}

Not yet built (see docs/GAME_ARCHITECTURE.md): every one of the 7
turn_order phases is now a real decision point (or fully automatic, for
Combat Resolution), including the one genuinely out-of-turn exchange
(alliance_invite/alliance_invite_response) -- what's NOT handled is a
target that never answers at all (no timeout/auto-decline; the inviter's
turn simply stays open indefinitely -- not yet exercised by the one demo
scenario, server.app._build_demo_session, which only ever has ONE human
faction in play, so a HUMAN-to-HUMAN invite never actually happens
there). Also not yet built: multiple simultaneous games (one GameSession
per server process for now); real auth/session management (a "join"
message is trusted at face value -- nothing stops two connections both
claiming the same faction, which would matter a lot more once a genuine
out-of-turn message like alliance_invite_response exists); client-
controlled PACING of playback is entirely a client-side concern once it
has a "bot_turn"/"combat_events" message's full event list (decided this
session) -- the server never paces delivery itself.
"""
from engine.bots.alliance_policy import accepts_invite
from engine.engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from engine.state import FactionMode, Phase
from .stepper import PhaseStepper

# turn_log event kinds a HUMAN's own drained (automatic) phases produce, and
# which message reports them: Combat Resolution's battles ("combat_events",
# roll-by-roll plus one battle_summary per battle), and Capture Territory /
# Deploy + Income ("turn_events"). The human's OWN purchase/move decisions
# aren't echoed back -- it just submitted them.
_BATTLE_KINDS = ('battle_event', 'battle_summary')
_AUTOMATIC_PHASE_KINDS = ('territory_captured', 'unit_deployed', 'income_collected', 'faction_eliminated')

# Phases where GameState.active_faction (a HUMAN one) is waiting on this
# module for a real decision -- see connect()/_decision_prompt.
_HUMAN_DECISION_PHASES = (Phase.PURCHASE, Phase.COMBAT_MOVE, Phase.NONCOMBAT_MOVE, Phase.ALLIANCES)


class GameSession:
    def __init__(self, engine, turn_log, bots=None):
        self.engine = engine
        self.turn_log = turn_log
        self.bots = bots or {}  # faction_code -> RandomBot, one per BOT faction in play
        # The all-bot "watch" mode ({"type": "watch"}/{"type": "next"}) --
        # see server/stepper.py for its own protocol.
        self.stepper = PhaseStepper(engine, turn_log, self.bots)
        # The ONE out-of-turn decision in the whole protocol -- everything
        # else is always made by GameState.active_faction, on its own
        # turn. Set only between a HUMAN inviting another HUMAN (see
        # _handle_alliance_action) and that target's own alliance_invite_
        # response, however long that takes -- {'inviter': faction_code,
        # 'target': faction_code} or None. A BOT target never sets this;
        # its accept/decline is still resolved synchronously via
        # engine.bots.alliance_policy.accepts_invite, same as bot-to-bot.
        self._pending_invite = None

    def connect(self, faction):
        """A client just identified itself as `faction` ("join"). Pure
        query/no mutation -- returns the messages to send it (always the
        current state; a "your_turn" too if it's already this faction's
        turn at a phase needing a decision -- e.g. reconnecting mid-turn,
        not just a fresh Purchase phase -- OR, taking priority over that,
        an "alliance_invite"/"alliance_invite_sent" resume if `faction` is
        either side of a currently-pending out-of-turn invite -- see
        _pending_invite_message). Safe to call even mid-Non-Combat Move:
        process_return_to_base already ran (see _drain_phases) before
        this faction's phase could ever be NONCOMBAT_MOVE with
        active_faction pointed at it, so _decision_prompt's legal_
        noncombat_move_options call below reflects that, not a stale
        pre-return-to-base snapshot. Same idea for ALLIANCES: reaching it
        at all (with active_faction still pointed here) already means
        every phase before it this turn is fully resolved."""
        gs = self.engine.game_state
        if faction not in gs.factions:
            return [self._error(faction, f'no such faction: {faction}')]
        if gs.factions[faction].mode != FactionMode.HUMAN:
            return [self._error(faction, f'{faction} is not a HUMAN-controlled faction')]

        messages = [self._state_message()]
        pending = self._pending_invite_message(faction)
        if pending is not None:
            messages.append(pending)
        elif faction == gs.active_faction and gs.phase in _HUMAN_DECISION_PHASES:
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
        if msg_type == 'watch':
            return self.stepper.watch()
        if msg_type == 'next':
            return self.stepper.next()
        if msg_type == 'stage_moves':
            return self.stepper.stage_moves(faction, msg.get('orders') or [])
        if msg_type == 'stage_purchase':
            return self.stepper.stage_purchase(faction, msg.get('orders') or [])
        if msg_type == 'purchase':
            return self._handle_purchase(faction, msg.get('orders') or [])
        if msg_type == 'combat_move':
            return self._handle_combat_move(faction, msg.get('orders') or [])
        if msg_type == 'noncombat_move':
            return self._handle_noncombat_move(faction, msg.get('orders') or [])
        if msg_type == 'alliance_action':
            return self._handle_alliance_action(faction, msg.get('action'), msg.get('target'))
        if msg_type == 'alliance_invite_response':
            return self._handle_alliance_invite_response(faction, msg.get('accept'))
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

    def _handle_alliance_action(self, faction, action, target):
        """`action`: "none" (do nothing this turn -- always legal, same
        as leaving a unit out of a Purchase/Combat Move/Non-Combat Move
        order list, just spelled as its own explicit value here since
        there's no list to leave empty), "invite" (requires `target`), or
        "withdraw". Unlike the other _handle_* methods, there is no
        advance_phase() call here to make afterward -- Alliances is the
        LAST phase (advance_phase() is already a documented no-op once
        there), so once this faction's one optional action is resolved
        there's nothing left to decide this turn; process_game_end_check
        runs directly (the same call _drain_phases' bot path always made
        here) and the turn is finished via _finish_turn, bypassing
        _continue_human_turn/_drain_phases entirely (re-entering those
        would just hit the ALLIANCES stop-and-return-False condition
        again, since nothing moved GameState.phase anywhere).

        "invite" against a BOT target resolves immediately, same as
        always -- engine.bots.alliance_policy.accepts_invite is a pure,
        synchronous function of the target's own alliance_strategy, no
        different from a bot inviting another bot. Against a HUMAN
        target, though, there is genuinely nobody to synchronously ask:
        this starts the one out-of-turn exchange in the whole protocol
        (see _pending_invite/_handle_alliance_invite_response) and
        returns immediately WITHOUT finishing `faction`'s turn -- no
        process_game_end_check, no _finish_turn. The turn stays open,
        awaiting the target's response, however long that takes; a
        second alliance_action from `faction` in the meantime is
        rejected (see the _pending_invite guard below)."""
        gs = self.engine.game_state
        if faction != gs.active_faction:
            return [self._error(faction, f"it is not {faction}'s turn")]
        if self._pending_invite is not None:
            return [self._error(faction, 'an alliance invite is already pending -- awaiting a response')]
        try:
            if action == 'invite':
                if not target:
                    raise ValueError("'invite' requires a target")
                if target not in self.engine.legal_alliance_options(faction)['eligible_invite_targets']:
                    raise ValueError(f'{target} is not a legal invite target for {faction} right now')
                if gs.factions[target].mode == FactionMode.HUMAN:
                    self._pending_invite = {'inviter': faction, 'target': target}
                    return [
                        {'type': 'alliance_invite_sent', 'to': faction, 'target': target},
                        {'type': 'alliance_invite', 'to': target, 'from': faction},
                    ]
                accepts = accepts_invite(self.engine, target, faction)
                self.engine.invite_to_alliance(faction, target, accepts)
            elif action == 'withdraw':
                self.engine.withdraw_from_alliance(faction)
            elif action != 'none':
                raise ValueError(f'unknown alliance action: {action!r}')
            # Also the phase guard for "none", which has no engine call
            # of its own above to raise for a wrong-phase attempt.
            self.engine.process_game_end_check(faction)
        except ValueError as e:
            return [self._error(faction, str(e))]
        return self._finish_turn(faction, [])

    def _handle_alliance_invite_response(self, faction, accept):
        """`faction` is answering a pending out-of-turn invite -- the
        one place in this protocol where the responder is NOT GameState.
        active_faction (that's still the inviter, mid-Alliances-phase,
        with its own turn paused since _handle_alliance_action started
        this). `accept`: True/False, required.

        Resolves the exact same engine call the synchronous BOT-target
        path always used (invite_to_alliance) -- just with the decision
        arriving from a real out-of-turn message instead of alliance_
        policy.accepts_invite; the engine itself never knew or cared
        which one supplied it (see invite_to_alliance's own docstring:
        "whichever bot/human logic controls target is responsible for
        it"). Clears _pending_invite FIRST, before calling anything else,
        so a second response (or a reconnect racing the first one) can't
        double-process the same invite. On success, finishes the
        INVITER's turn, not the responder's -- it was never the
        responder's turn to begin with, and still isn't."""
        if self._pending_invite is None or faction != self._pending_invite['target']:
            return [self._error(faction, 'no alliance invite is pending for you')]
        if accept is None:
            return [self._error(faction, "'accept' is required (true or false)")]
        pending = self._pending_invite
        self._pending_invite = None
        inviter = pending['inviter']
        try:
            self.engine.invite_to_alliance(inviter, faction, bool(accept))
            self.engine.process_game_end_check(inviter)
        except ValueError as e:
            # Something an eligibility pre-check at invite-send time
            # couldn't have caught -- most likely accepting would now
            # exceed the effective max alliance size, which legal_
            # alliance_options deliberately never pre-filters for (see
            # its own docstring). Report it to the responder, whose
            # message triggered this call, and let the inviter's turn
            # resume as a fresh decision -- their one action was never
            # actually consumed, since invite_to_alliance only marks
            # that once every check upstream of target_accepts already
            # passed.
            return [self._error(faction, str(e)), self._decision_prompt(inviter)]
        return self._finish_turn(inviter, [])

    def _pending_invite_message(self, faction):
        """If `faction` is either side of the one currently-pending
        out-of-turn invite, the message to resend it on a fresh "join"
        -- the inviter is told their invite is still awaiting a response
        (NOT a fresh ALLIANCES your_turn -- they can't act again until
        this resolves, see _handle_alliance_action's own guard), the
        target gets the same alliance_invite prompt a live connection
        got when it was first sent. None if there's no pending invite,
        or `faction` isn't part of the one that exists."""
        pending = self._pending_invite
        if pending is None:
            return None
        if faction == pending['inviter']:
            return {'type': 'alliance_invite_sent', 'to': faction, 'target': pending['target']}
        if faction == pending['target']:
            return {'type': 'alliance_invite', 'to': faction, 'from': pending['inviter']}
        return None

    def _continue_human_turn(self, faction):
        """Drains as much of `faction`'s own turn as possible (bot=None),
        starting from wherever GameState.phase currently is. Stops at
        Combat Move, Non-Combat Move, or Alliances -- returning that
        phase's "your_turn" prompt, awaiting that decision -- or, for the
        rare case _drain_phases runs all the way through on its own
        (bot=None never actually reaches Alliances via this path today,
        since every earlier phase either interrupts or leaves something
        for the caller to have already handled -- see _drain_phases),
        finishes the turn via _finish_turn. Any battle_event entries
        logged during THIS call (i.e. from Combat Resolution, if Combat
        Move is what was just confirmed) are reported as "combat_events"
        first, even when the drain stops again right after at Non-Combat
        Move -- a human's own attack landing must never go unreported
        just because Non-Combat Move is now also an interrupt point; it
        can no longer be assumed, as it could when only Combat Move
        stopped the drain, that "not finished" means "nothing happened
        yet"."""
        start = len(self.turn_log.events)
        finished = self._drain_phases(faction, bot=None)

        new_events = self.turn_log.events[start:]
        combat_events = [e for e in new_events if e['kind'] in _BATTLE_KINDS]
        turn_events = [e for e in new_events if e['kind'] in _AUTOMATIC_PHASE_KINDS]
        messages = []
        if combat_events:
            messages.append({'type': 'combat_events', 'faction': faction, 'events': combat_events})
        if turn_events:
            messages.append({'type': 'turn_events', 'faction': faction, 'events': turn_events})
        if not finished:
            messages.append(self._decision_prompt(faction))
            return messages
        return self._finish_turn(faction, messages)

    def _finish_turn(self, faction, messages):
        """Shared tail for whenever `faction`'s turn has nothing left to
        decide -- process_game_end_check has already run by this point
        (either _drain_phases' bot path, or _handle_alliance_action).
        `messages` already carries whatever this call should report
        before the state/game-over/next-turn wrap-up (typically nothing
        for _handle_alliance_action, or a "combat_events" entry for
        _continue_human_turn -- see there). Appends the resulting state,
        then either game_over or hands off to advance_turn() and however
        many bot turns follow before the next human's turn."""
        gs = self.engine.game_state
        messages.append(self._state_message())
        if gs.game_over:
            messages.append({'type': 'game_over'})
            return messages
        self.engine.advance_turn()
        return messages + self._play_bot_turns_until_human_or_game_over()

    def _drain_phases(self, faction, bot):
        """Runs `faction`'s turn from wherever GameState.phase currently
        is -- UNLESS it reaches Combat Move, Non-Combat Move, or
        Alliances with `bot=None` (a human awaiting that decision), in
        which case it stops right there and returns False without
        touching that phase's actual decision at all; the caller is
        responsible for prompting and, once a decision arrives, resuming
        (Combat Move/Non-Combat Move do this by calling back into here
        after advancing off the phase themselves -- see _handle_combat_
        move/_handle_noncombat_move; Alliances instead finishes the turn
        directly, since it's the last phase -- see _handle_alliance_
        action). Returns True if it ran the WHOLE way through to
        Alliances AND handled it too (only ever happens for a bot, or in
        principle if `bot=None` reached Alliances via some future caller
        that isn't _continue_human_turn -- see its own docstring).

        `bot`, if given, makes its own Purchase/Combat Move/Non-Combat
        Move/Alliance decisions (a full bot turn, since GameState.phase
        starts back at PURCHASE right after advance_turn -- see
        _play_bot_turns_until_human_or_game_over) -- for a bot, none of
        Combat Move/Non-Combat Move/Alliances ever triggers the stop-and-
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
        while True:
            if gs.phase == Phase.COMBAT_MOVE and bot is None:
                return False
            if gs.phase == Phase.NONCOMBAT_MOVE and bot is None:
                # Always automatic, even for a human -- it's not a
                # decision, just bookkeeping that has to happen before
                # legal_noncombat_move_options (sent by the "your_turn"
                # prompt right after this returns) is even meaningful to
                # compute. Then stop and wait for the real decision, same
                # shape as Combat Move above.
                self.engine.process_return_to_base(faction)
                return False
            if gs.phase == Phase.ALLIANCES:
                if bot is None:
                    return False
                bot.take_alliance_phase()
                self.engine.process_game_end_check(faction)
                return True
            if gs.phase == Phase.PURCHASE:
                if bot is not None:
                    bot.take_purchase_phase()
            elif gs.phase == Phase.COMBAT_MOVE:
                bot.take_combat_move_phase()
            elif gs.phase == Phase.COMBAT_RESOLUTION:
                self.engine.resolve_combat(faction)
            elif gs.phase == Phase.NONCOMBAT_MOVE:
                # RandomBot.take_noncombat_move_phase() already calls
                # process_return_to_base itself, first thing -- must NOT
                # also be called here too (a second call raises, "already
                # processed return-to-base this turn"). Only reachable
                # with bot is not None -- the bot=None case returned
                # above already.
                bot.take_noncombat_move_phase()
            elif gs.phase == Phase.CAPTURE:
                self.engine.process_capture_territory(faction)
                self.engine.process_elimination_check()
            elif gs.phase == Phase.DEPLOY_INCOME:
                self.engine.deploy_and_collect_income(faction)
            self.engine.advance_phase()

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
        Non-Combat Move/Alliances decision later the same turn. Always
        type "your_turn" either way (it's still this faction's own
        turn); the client tells them apart by "phase" and reads whichever
        legal_* key is present. For NONCOMBAT_MOVE specifically, this is
        only ever called after _drain_phases has already run process_
        return_to_base for this faction this turn (see there) -- so
        legal_noncombat_move_options genuinely reflects what's left to
        decide, not units that already got automatically sent back to
        base."""
        gs = self.engine.game_state
        msg = {'type': 'your_turn', 'faction': faction, 'phase': gs.phase.value}
        if gs.phase == Phase.PURCHASE:
            sc_targets, other_targets = self.engine.legal_purchase_targets(faction)
            msg['legal_purchase_targets'] = {'sc_targets': sc_targets, 'other_targets': other_targets}
        elif gs.phase == Phase.COMBAT_MOVE:
            msg['legal_combat_moves'] = self.engine.legal_combat_move_options(faction)
        elif gs.phase == Phase.NONCOMBAT_MOVE:
            msg['legal_noncombat_moves'] = self.engine.legal_noncombat_move_options(faction)
        elif gs.phase == Phase.ALLIANCES:
            msg['legal_alliance_options'] = self.engine.legal_alliance_options(faction)
        return msg

    @staticmethod
    def _error(faction, message):
        return {'type': 'error', 'to': faction, 'message': message}
