"""
PhaseStepper: the watcher's plan-then-execute play mode. Every faction in
play is a BOT; the game stops at each phase with that phase's orders
already decided and QUEUED (a "phase_queue" message), and only executes
them when a watcher sends "next" -- then it moves on and queues the
following phase.

This is the pattern the real player UI will share: a human's orders would
land in the same queue (staged via the engine's submit_*), just decided by
a person instead of RandomBot.plan_*; "next" is the same confirm step.

Protocol (kept apart from GameSession's human protocol -- see session.py):

Client -> server:
    {"type": "watch"}   join as a spectator; answered with the current
                        state and the queue awaiting execution.
    {"type": "next"}    execute the queued phase, then queue the next one.
    {"type": "stage_moves", "orders": [{"unit_id", "path"} | {"unit_id", "destination"}, ...]}
                        a HUMAN faction's Combat Move ({"unit_id", "path"}) or
                        Non-Combat Move ({"unit_id", "destination"}): replace the
                        staged move list with this COMPLETE one, one order per unit.
                        Same validation/reply/commit pattern as stage_purchase.
    {"type": "diplomacy_action", "faction": "NAA", "action": "invite"|"withdraw"|"surrender", "target": "UE"}
                        a HUMAN faction's Diplomacy phase: one action, carried out AT ONCE
                        (the client makes each a long-click). "invite" / "withdraw" is the phase's
                        one alliance action; "surrender" forces `target` to surrender (any
                        number of times, before or after the alliance action) when a ground holds
                        (engine.surrender_grounds). Answered with a "diplomacy_result" (the events
                        it logged, for the Events box), the refreshed phase_queue and the state;
                        an illegal one is an "error" plus the unchanged queue. "next" then just
                        ends the phase.
    {"type": "surrender", "faction": "NAA"}
                        the Settings 'Surrender' action: `faction` (a HUMAN) eliminates itself,
                        outright -- unlike "diplomacy_action"'s "surrender" (forcing SOMEONE ELSE
                        out on grounds, only during the demander's own Diplomacy phase), this is
                        always legal for `faction` itself, from anywhere in the game, any phase,
                        whether or not it is `faction`'s turn (GameEngine.surrender). A long-click
                        in the client, kept well away from an accidental tap. Answered with a
                        "self_surrender_result" (the events it logged), the state, and -- if this
                        was the last active faction or leaves everyone left mutually allied -- a
                        "game_over"; otherwise "next" carries on queuing the game exactly as before
                        (this faction just never comes up again). An "error" if illegal (already
                        eliminated, the game is already over, or not a HUMAN/BOT faction).
    {"type": "propose_armistice", "faction": "NAA"}
                        the Settings 'Propose Armistice' action: `faction` (a HUMAN, active or
                        already eliminated -- see below) proposes ending the game right now, no
                        winner declared. Every other ACTIVE faction must agree: a BOT always does,
                        at once; a HUMAN is asked (a popup, "respond_armistice" answers it) and
                        "next" is refused game-wide until every asked human has. If everyone agrees,
                        GameEngine.end_by_armistice runs and the game is over. A single decline ends
                        the proposal (no game-over) and is announced to everyone. Only one proposal
                        may be pending at a time. An ELIMINATED human may still propose one (they
                        have nothing left to play for but may want to see the game end) -- everyone
                        still active is asked the same way; the proposer isn't, since proposing
                        already counts as agreeing.
    {"type": "respond_armistice", "faction": "NAA", "accept": true}
                        a HUMAN's answer to a pending "armistice_proposed" prompt. An "error" if
                        `faction` has nothing pending to answer.
    {"type": "respond_invitation", "faction": "NAA", "accept": true}
                        the HUMAN target of a bot's invitation answering it (see
                        below); "next" is refused until it does.
    {"type": "stage_purchase", "orders": [{"unit_type", "qty", "deploy_at"}, ...]}
                        a HUMAN faction's Purchase phase: replace the staged
                        purchase list (the COMPLETE list, like the human
                        protocol's "purchase"). Validated by the engine
                        (submit_purchases); answered with the refreshed
                        phase_queue, or an "error" plus the unchanged queue.
                        "next" then confirms it -- irreversibly.

A faction in HUMAN mode is a player, not a bot: its phases are queued the same
way, but nothing is decided for it. Its Purchase queue carries a "human" block
(engine.purchase_options: treasury, legal targets with remaining capacity,
staged orders with costs); its Combat/Non-Combat Move queues carry the legal moves
and staged moves; its Diplomacy queue carries {kind: "diplomacy", members, options
(eligible_invite_targets, can_withdraw, alliance_action_used), surrender (the
factions it may force to surrender and why), game_would_end}. Its Capture and
Deploy phases are automatic.

A bot's Diplomacy phase that INVITES a human faction cannot be resolved by policy:
its phase_queue carries an "invitation" {from, to, members, answered, accepts},
the human answers with respond_invitation, the queue is re-sent with the answer
(shown in its alliance_plan event), and only then will "next" execute it. A bot
that means to force a surrender queues a "surrender_plan" event beside it.

Server -> client (always broadcast to watchers):
    {"type": "phase_queue", "faction": "AAC", "phase": "PURCHASE",
     "events": [...], "skipped": ["COMBAT_MOVE"]}
        What `faction` WILL do this phase, in turn_log's event shapes
        (purchase / combat_move / noncombat_move / alliance_plan / surrender_plan), plus
        'battle_preview' (the battles about to be fought and who is in
        them) or 'bombardment_preview' (rules.json's combat.cruiser_bombardment
        -- the Cruiser and who it's about to fire at) for Combat Resolution,
        or -- for the automatic Capture and Deploy + Income phases -- the
        events a dry run of the phase produces. `skipped`: phases the game
        passed over just before this one (e.g. Combat Move on a faction's
        first turn).
    Every faction's turn opens with a phase "START_OF_TURN" (also not a
    GameState phase): its one event, start_of_turn {faction, round, turn,
    turns_in_round}, just says which round and which turn is starting;
    executing it changes nothing but moves on to that faction's Purchase.
    A Non-Combat Move whose faction has aircraft to send home is queued as
    two steps: first phase "RETURN_TO_BASE" (not a GameState phase; its
    events are the return_to_base flights the game makes automatically),
    then the ordinary "NONCOMBAT_MOVE" queue, planned once they have landed.
    Combat Resolution is queued one bombardment, then one battle, at a time --
    every declared bombardment fires (each its own phase_queue, holding that
    bombardment's bombardment_preview and a "bombardment": {"index": i, "count": n})
    before any battle is even previewed; only once none remain does the phase
    queue battles the usual way (battle_preview and a "battle": {"index": i,
    "count": n}). "next" fights whichever is queued, and the phase stays
    Combat Resolution until the last battle.
    {"type": "phase_result", "faction": ..., "phase": ..., "events": [...]}
        What executing that phase actually logged (for Combat Resolution:
        the roll-by-roll events and one battle_summary per battle).
    {"type": "state", "game_state": ...}   sent after every executed phase.
    {"type": "self_surrender_result", "faction": "NAA", "events": [...]}
        The events a "surrender" logged (self_surrender, faction_eliminated) -- shown in the Events box.
    {"type": "armistice_proposed", "from": "NAA", "awaiting": ["UE", "GPC"]}
        A "propose_armistice" was accepted for consideration: `awaiting` lists the HUMAN factions still
        asked to answer (every BOT already has, synchronously). Every asked human's client shows the
        popup; watchers just see it happen. If `awaiting` is already empty (every other active faction
        was a bot), this is followed immediately by the game ending -- see "game_over" below. `from` is
        null for a pure spectator's own proposal (nobody's own faction -- see propose_armistice below).
    {"type": "armistice_resolved", "from": "NAA", "accepted": true, "declined_by": null, "events": [...]}
        The pending proposal's outcome: `accepted` false means one human declined (`declined_by` names
        them, and the game continues, unaffected -- this reply also carries `cooldown_until_round`: `from`
        can't propose another armistice until GameState.round_number reaches it, ARMISTICE_COOLDOWN_ROUNDS
        rounds after the one it was declined in -- see propose_armistice below); true means everyone
        agreed (an "events" armistice entry, and the game is over -- see "game_over" below).
    {"type": "game_over", "report": [{faction, seat_type, victory_status, elimination_reason,
     eliminated_by, round_eliminated, rounds_in_game, strategic_centers, territory_mpc, units_produced,
     units_destroyed, alliance_history, bot_type, bot_strategy, alliance_strategy, alliance_behavior}, ...]}
        Ends the game. `report` (server/report.py's build_game_report) is one row per seat, already
        sorted (Victory status, then Strategic Centers, Territory MPC, Units produced, Units destroyed)
        for the client's Game Over panel.
    {"type": "error", "message": ...}

Settings actions, client -> server (handled by GameSession, not this class -- see its own docstring):
    {"type": "surrender", "faction": "NAA"}
    {"type": "propose_armistice", "faction": "NAA"}          -- faction omitted/null: a pure spectator
    {"type": "respond_armistice", "faction": "UE", "accept": true}
        propose_armistice is refused (an "error") while another proposal is already pending, once the
        game is over, or -- new this session -- while the proposer (the same faction, or the same "null"
        spectator identity) is still cooling down from their last proposal having been declined: see
        GameSession.ARMISTICE_COOLDOWN_ROUNDS and armistice_resolved's cooldown_until_round above. Only
        the specific proposer who was turned down is cooled down; anyone else may propose freely.
"""
import copy

from engine.bots.alliance_policy import accepts_invite
from engine.engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from engine.state import FactionMode, Phase
from engine.turn_log import TurnLog
from .report import build_game_report

_PHASES = list(Phase)

# A queue step of its own, not a GameState phase: the automatic return-to-base
# that opens Non-Combat Move (see PhaseStepper._plan_current_phase).
RETURN_TO_BASE = 'RETURN_TO_BASE'
# Another: the announcement that opens each faction's turn, ahead of Purchase.
START_OF_TURN = 'START_OF_TURN'


class PhaseStepper:
    def __init__(self, engine, turn_log, bots):
        self.engine = engine
        self.turn_log = turn_log
        self.bots = bots  # faction -> RandomBot; a HUMAN faction has none
        self._queue = None  # the phase_queue message awaiting "next"
        self._skipped_before = []
        self._alliance_plan = None  # a bot's whole Diplomacy plan (see RandomBot.plan_diplomacy_phase)
        self._bombardments = None  # the current Combat Resolution's [(cruiser_unit_id, territory_id), ...]
        self._bombardment_index = 0  # which of them is queued now -- fought before self._battles, always
        self._battles = None       # the current Combat Resolution's [(territory_id, battle_type), ...]
        self._battle_index = 0     # which of them is queued now
        self._combat_resolution_begun = False  # engine.begin_combat_resolution called yet, this instance?
        self._alliance_plan_for = None  # (faction, global_turn) the current _alliance_plan belongs to
        self._invitation = None    # a bot's invitation to a human awaiting its answer: {from, to, answered}
        self._turn_announced = None  # (faction, global_turn) whose Start of Turn has been executed

    # ---- protocol entry points -------------------------------------------

    def invalidate_queue(self):
        """Drops the cached queue so the next watch()/next() rebuilds it from scratch -- for a change
        that happened OUTSIDE this class's own step-by-step flow (GameSession's self-surrender and
        armistice handling), which may have made the current queue stale (e.g. it named a faction that
        is no longer in the game, or its legal_surrender_targets no longer holds)."""
        self._queue = None

    def watch(self):
        """Current state, plus the queue awaiting execution (planned now if
        this is the very first join)."""
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        gs = self.engine.game_state
        if gs.game_over:
            return [self._state_message(), self._game_over_message()]
        if self._queue is None:
            self._plan_current_phase()
        return [self._state_message(), self._queue]

    def stage_purchase(self, faction, raw_orders):
        """A human's whole staged purchase list. Replies with the refreshed
        queue (so every watching client updates), or an error plus the
        unchanged queue so the client can resync."""
        gs = self.engine.game_state
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        if self._queue is None:
            self._plan_current_phase()
        if faction not in gs.factions or not self._is_human(faction):
            return [self._error(f'{faction} is not a human-controlled faction')]
        if faction != gs.active_faction or gs.phase != Phase.PURCHASE or self._queue['phase'] != Phase.PURCHASE.value:
            return [self._error(f"it is not {faction}'s Purchase phase")]
        try:
            orders = [PurchaseOrder(o['unit_type'], int(o['qty']), int(o['deploy_at'])) for o in raw_orders]
        except (KeyError, TypeError, ValueError) as e:
            return [self._error(f'malformed order: {e}'), self._queue]
        try:
            self.engine.submit_purchases(faction, orders)
        except ValueError as e:
            return [self._error(str(e)), self._queue]
        self._plan_current_phase()  # rebuilds the queue from what is now staged
        return [self._queue]

    def stage_moves(self, faction, raw_orders):
        """A human's whole staged combat-move / non-combat-move list."""
        gs = self.engine.game_state
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        if self._queue is None:
            self._plan_current_phase()
        if faction not in gs.factions or not self._is_human(faction):
            return [self._error(f'{faction} is not a human-controlled faction')]
        phase = gs.phase
        if faction != gs.active_faction or phase not in (Phase.COMBAT_MOVE, Phase.NONCOMBAT_MOVE) \
                or self._queue['phase'] != phase.value:
            return [self._error(f"it is not {faction}'s move phase")]
        try:
            if phase == Phase.COMBAT_MOVE:
                orders = [CombatMoveOrder(int(o['unit_id']), [int(t) for t in o['path']]) for o in raw_orders]
            else:
                orders = [NonCombatMoveOrder(int(o['unit_id']), int(o['destination'])) for o in raw_orders]
        except (KeyError, TypeError, ValueError) as e:
            return [self._error(f'malformed order: {e}'), self._queue]
        try:
            if phase == Phase.COMBAT_MOVE:
                self.engine.submit_combat_moves(faction, orders)
            else:
                self.engine.submit_noncombat_moves(faction, orders)
        except (ValueError, KeyError) as e:  # KeyError: an id the engine has no such territory/unit for
            return [self._error(f'illegal move: {e}'), self._queue]
        self._plan_current_phase()
        return [self._queue]

    def diplomacy_action(self, faction, action, target):
        """One action of a human's Diplomacy phase, carried out at once: invite, withdraw or
        surrender. Replies with the events it logged (a "diplomacy_result"), the refreshed queue
        and the state -- or an error plus the unchanged queue."""
        gs = self.engine.game_state
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        if self._queue is None:
            self._plan_current_phase()
        if faction not in gs.factions or not self._is_human(faction):
            return [self._error(f'{faction} is not a human-controlled faction')]
        if faction != gs.active_faction or gs.phase != Phase.DIPLOMACY or self._queue['phase'] != Phase.DIPLOMACY.value:
            return [self._error(f"it is not {faction}'s Diplomacy phase")]
        if action not in ('invite', 'withdraw', 'surrender'):
            return [self._error(f'unknown diplomacy action {action!r}'), self._queue]
        engine = self.engine
        start = len(self.turn_log.events)
        try:
            if action == 'invite':
                if not target or target not in engine.legal_alliance_options(faction)['eligible_invite_targets']:
                    raise ValueError(f'{target} is not a legal invite target for {faction} right now')
                engine.invite_to_alliance(faction, target, accepts_invite(engine, target, faction))
            elif action == 'withdraw':
                engine.withdraw_from_alliance(faction)
            else:
                if not target:
                    raise ValueError("'surrender' needs a target")
                engine.demand_surrender(faction, target)
        except ValueError as e:
            return [self._error(str(e)), self._queue]
        events = self.turn_log.events[start:]
        if action == 'invite' and not any(e['kind'] in ('alliance_joined', 'alliance_declined') for e in events):
            events = events + [{'kind': 'alliance_declined', 'faction': faction, 'target': target}]
        self._plan_current_phase()
        return [{'type': 'diplomacy_result', 'faction': faction, 'action': action, 'target': target, 'events': events},
                self._queue, self._state_message()]

    def respond_invitation(self, faction, accept):
        """The human target of a bot's invitation answers it."""
        inv = self._invitation
        if inv is None or inv['to'] != faction or inv['answered']:
            return [self._error('no invitation is waiting for your answer')]
        if accept is None:
            return [self._error("'accept' is required (true or false)"), self._queue]
        self._alliance_plan['accepts'] = bool(accept)
        inv['answered'] = True
        self._plan_current_phase()
        return [self._queue]

    def next(self):
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        gs = self.engine.game_state
        if gs.game_over:
            return [self._game_over_message()]
        if self._queue is None:
            self._plan_current_phase()
        if self._invitation is not None and not self._invitation['answered']:
            return [self._error(f"{self._invitation['to']} must answer {self._invitation['from']}'s invitation first"), self._queue]
        return self._execute_queued_phase()

    # ---- execute ---------------------------------------------------------

    def _execute_queued_phase(self):
        gs = self.engine.game_state
        faction, queued = self._queue['faction'], self._queue['phase']
        start = len(self.turn_log.events)
        messages = []
        stay = False  # True: the phase isn't over (more battles to fight), so don't advance it
        if queued == START_OF_TURN:
            # Announcement only: record it and go on to Purchase. GameState.phase doesn't move.
            self._turn_announced = self._turn_key(faction)
            self.turn_log.events.append(self._queue['events'][0])
            phase = None
        elif queued == RETURN_TO_BASE:
            # Its own step, ahead of the rest of Non-Combat Move: air units
            # that fought this turn fly home. GameState.phase doesn't move.
            # `faction` was active when this was queued (_plan_current_phase only ever builds this step
            # for one that still is) -- but the Settings 'Surrender' action can take it out of the game
            # in the meantime, between the queue being built and this "next"; skip if so, same as
            # _commit's own active guards below.
            if faction in gs.active_factions():
                self.engine.process_return_to_base(faction)
            phase = None
        else:
            phase = gs.phase
            stay = self._commit(faction, phase)
        messages.append({'type': 'phase_result', 'faction': faction, 'phase': queued,
                         'events': self.turn_log.events[start:]})

        self._queue = None
        if phase == Phase.DIPLOMACY:
            self._alliance_plan = None
            self._alliance_plan_for = None
            self._invitation = None
        if phase is None or stay:
            self._skipped_before = []
        elif phase == Phase.DIPLOMACY:
            if gs.game_over:
                return messages + [self._state_message(), self._game_over_message()]
            self.engine.advance_turn()
            self._skipped_before = []
        else:
            before = _PHASES.index(phase)
            self.engine.advance_phase()
            self._skipped_before = _PHASES[before + 1:_PHASES.index(gs.phase)]
        self._plan_current_phase()
        return messages + [self._queue, self._state_message()]

    def _commit(self, faction, phase):
        """Executes the queued phase. `active` guards every branch: a faction can be eliminated at any
        point now, not just via another's surrender demand in ITS OWN Diplomacy phase (still true) --
        the Settings 'Surrender' action (GameEngine.surrender) is out-of-band and can strike at any
        moment, including mid this very faction's OWN turn, before its currently-queued phase (Purchase,
        Combat Move, whatever) ever gets committed. When that happens, every remaining phase call for it
        this turn is simply a no-op, same handling as engine.bots.driver already used for the
        Capture/Deploy/Diplomacy phases -- extended here to cover every phase, since now any of them can
        be the one left holding a stale queue."""
        engine, bot = self.engine, self.bots.get(faction)
        active = faction in engine.game_state.active_factions()
        if phase == Phase.PURCHASE:
            if active:
                engine.confirm_purchases(faction)
        elif phase == Phase.COMBAT_MOVE:
            if active:
                engine.confirm_combat_moves(faction)
        elif phase == Phase.COMBAT_RESOLUTION:
            if active:
                if not self._combat_resolution_begun:
                    engine.begin_combat_resolution(faction)
                    self._combat_resolution_begun = True
                if self._bombardments:
                    return self._commit_one_bombardment(faction)
                return self._commit_one_battle(faction)
            self._bombardments = None
            self._battles = None
            self._combat_resolution_begun = False
        elif phase == Phase.NONCOMBAT_MOVE:
            if active:
                engine.confirm_noncombat_moves(faction)
        elif phase == Phase.CAPTURE:
            if active:
                engine.process_capture_territory(faction)
        elif phase == Phase.DEPLOY_INCOME:
            if active:
                engine.deploy_and_collect_income(faction)
        elif phase == Phase.DIPLOMACY:
            if active:
                if bot is not None:  # a human's actions were already carried out, one by one
                    self._apply_diplomacy(faction, bot)
                engine.process_game_end_check(faction)
            else:
                engine.game_state.game_over = engine.would_game_end()
        return False

    def _apply_diplomacy(self, faction, bot):
        """Executes a bot's queued Diplomacy plan: its alliance action and surrender demands, through
        its own commit. An invite to the HUMAN uses their answer (already in the plan)."""
        bot.commit_diplomacy_phase(self._alliance_plan or {'action': 'none'})

    def _alliance_key(self, faction):
        return (faction, self.engine.game_state.global_turn)

    def _commit_one_bombardment(self, faction):
        """Combat Resolution goes bombardment by bombardment FIRST, each its
        own queue step (so a watcher can pause/preview per bombardment,
        exactly like a battle) -- rules.json's combat.cruiser_bombardment:
        every one of these fires before self._battles' real battles. Fights
        the queued one; returns True while more remain. Only ever called
        while self._bombardments is non-empty (see _commit); the
        once-per-turn begin_combat_resolution gate is _commit's own job,
        since it must fire even when there's nothing declared at all.

        Once the last bombardment fires, self._battles is STILL None -- it's
        only computed lazily, once bombardments are exhausted (see
        _plan_current_phase) -- so this peeks at declared_battles itself to
        decide whether to report "stay" (keeping Combat Resolution queued so
        the next planning pass can queue the first battle) rather than
        wrongly letting the caller advance the phase with a real battle
        still undeclared."""
        engine = self.engine
        cruiser_unit_id, _territory_id = self._bombardments[self._bombardment_index]
        engine.resolve_one_bombardment(faction, cruiser_unit_id)
        self._bombardment_index += 1
        if self._bombardment_index < len(self._bombardments):
            return True
        self._bombardments = None
        return bool(engine.declared_battles(faction))

    def _commit_one_battle(self, faction):
        """Combat Resolution goes battle by battle (each its own queue step, so
        a watcher can pause between them): fights the queued one. Returns True
        while more battles remain in the phase. With none declared, the phase
        is simply done -- begin_combat_resolution, and every declared
        bombardment, already ran via _commit's own gate and
        _commit_one_bombardment before this is ever reached, EXCEPT when this
        turn declared none at all: _commit_one_bombardment is then never
        called, so self._bombardments is still [] (falsy, but not None) --
        left alone, `self._bombardments is None` (_plan_current_phase's own
        gate for recomputing it) would stay permanently False from here on,
        forever skipping declared_bombardments for every faction's every
        future turn this same PhaseStepper instance runs. Reset it here too,
        same as self._battles, whichever branch actually ends the phase."""
        engine = self.engine
        if not self._battles:
            self._bombardments = None
            self._battles = None
            self._combat_resolution_begun = False  # Combat Resolution is now fully done, bombardments and battles alike
            return False
        territory_id, battle_type = self._battles[self._battle_index]
        engine.resolve_one_battle(faction, territory_id, battle_type)
        self._battle_index += 1
        if self._battle_index < len(self._battles):
            return True
        self._bombardments = None
        self._battles = None
        self._combat_resolution_begun = False
        return False

    # ---- plan ------------------------------------------------------------

    def _plan_current_phase(self):
        """Has the active bot decide the current phase and stage it (nothing
        is committed), then builds the phase_queue message describing it."""
        engine, gs = self.engine, self.engine.game_state
        faction, phase = gs.active_faction, gs.phase
        bot = self.bots.get(faction)
        human = self._is_human(faction)
        extra = {}

        if phase == Phase.PURCHASE and faction in gs.active_factions() and self._turn_announced != self._turn_key(faction):
            self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': START_OF_TURN,
                           'events': [self._start_of_turn_event(faction)], 'skipped': []}
            return
        if faction not in gs.active_factions():
            events = []  # eliminated during its own turn; nothing left to do (see _commit)
        elif phase == Phase.PURCHASE:
            if human:
                # The player composes it; keep whatever is staged already (a re-plan
                # after each stage_purchase must not wipe it).
                events = [engine.staged_purchase_event(faction)]
                extra['human'] = engine.purchase_options(faction)
            else:
                bot.plan_purchase_phase()
                events = [engine.staged_purchase_event(faction)]
        elif phase == Phase.COMBAT_MOVE:
            if human:
                # The player composes it; a re-plan after each stage_moves must keep it.
                extra['human'] = self._move_block(faction, 'combat')
            else:
                bot.plan_combat_move_phase()
            events = [engine.staged_combat_move_event(faction)]
        elif phase == Phase.COMBAT_RESOLUTION:
            # Bombardments queue -- and fully drain -- before battles ever get a look in
            # (rules.json's combat.cruiser_bombardment: the very start of Combat Resolution).
            if self._bombardments is None:
                self._bombardments = engine.declared_bombardments(faction)
                self._bombardment_index = 0
            events = []
            if self._bombardments:
                events = [engine.bombardment_preview(faction, *self._bombardments[self._bombardment_index])]
            else:
                if self._battles is None:
                    self._battles = engine.declared_battles(faction)
                    self._battle_index = 0
                if self._battles:
                    events = [engine.battle_preview(faction, *self._battles[self._battle_index])]
        elif phase == Phase.NONCOMBAT_MOVE:
            if not engine.has_processed_return_to_base(faction):
                homeward = self._dry_run(lambda sim: sim.process_return_to_base(faction))
                if homeward:
                    # Queue the flight home on its own first; the rest of the
                    # phase is planned once it has run (from the new positions).
                    self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': RETURN_TO_BASE,
                                   'events': homeward, 'skipped': [p.value for p in self._skipped_before]}
                    return
            if human:
                if not engine.has_processed_return_to_base(faction):
                    engine.process_return_to_base(faction)  # a bot's plan does this itself
                extra['human'] = self._move_block(faction, 'noncombat')
            else:
                bot.plan_noncombat_move_phase()
            events = [engine.staged_noncombat_move_event(faction)]
        elif phase == Phase.CAPTURE:
            events = self._dry_run(lambda sim: sim.process_capture_territory(faction))
        elif phase == Phase.DEPLOY_INCOME:
            events = self._dry_run(lambda sim: sim.deploy_and_collect_income(faction))
        else:  # DIPLOMACY
            key = self._alliance_key(faction)
            if human:
                events = []  # the player acts at once (diplomacy_action); nothing is queued
                extra['human'] = self._diplomacy_block(faction)
            else:
                if self._alliance_plan_for != key or self._alliance_plan is None:
                    # A fresh plan for this turn (re-planning after an answer must keep the
                    # one already made -- a bot's pick is random).
                    self._alliance_plan = bot.plan_diplomacy_phase()
                    self._alliance_plan_for = key
                    self._invitation = None
                    target = self._alliance_plan.get('target')
                    if self._alliance_plan['action'] == 'invite' and self._is_human(target):
                        self._alliance_plan['accepts'] = None  # only the player can say
                        self._invitation = {'from': faction, 'to': target, 'answered': False}
                plan = self._alliance_plan
                events = [{'kind': 'alliance_plan', 'faction': faction, 'action': plan['action'],
                           **({'target': plan['target'], 'accepts': plan.get('accepts')} if plan['action'] == 'invite' else {})}]
                if plan.get('demands') or plan.get('demand_if_declined'):
                    events.append({'kind': 'surrender_plan', 'faction': faction, 'targets': list(plan.get('demands', [])),
                                   'if_declined': plan['target'] if plan.get('demand_if_declined') else None,
                                   'win': bool(plan.get('win'))})
                if self._invitation is not None:
                    members = sorted(engine.alliance_members(faction))
                    extra['invitation'] = {**self._invitation, 'members': members,
                                           'accepts': self._alliance_plan.get('accepts')}

        self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': phase.value,
                       'events': events, 'skipped': [p.value for p in self._skipped_before], **extra}
        if phase == Phase.COMBAT_RESOLUTION and self._bombardments:
            self._queue['bombardment'] = {'index': self._bombardment_index, 'count': len(self._bombardments)}
        elif phase == Phase.COMBAT_RESOLUTION and self._battles:
            self._queue['battle'] = {'index': self._battle_index, 'count': len(self._battles)}

    def _turn_key(self, faction):
        return (faction, self.engine.game_state.global_turn)

    def _start_of_turn_event(self, faction):
        """Round and turn, in the client's own terms: a round is one turn for each faction still in
        play, and the turn is this faction's place in it. `round` is GameState.round_number, the real,
        authoritative counter (GameEngine.advance_turn increments it on wraparound) -- NOT global_turn //
        len(active_factions()) (this method's own former formula): that gives a WRONG, inflated answer
        once an earlier elimination has already shrunk active_factions() partway through the game, since
        the same global_turn then divides by a smaller n than it should. A stored counter that only ever
        increments at the moment a lap genuinely completes has no such problem -- see GameState.
        round_number's own docstring, and server/report.py's round_eliminated/rounds_in_game, which read
        the very same field for exactly this reason."""
        gs = self.engine.game_state
        order = gs.active_factions()
        n = max(1, len(order))
        return TurnLog.start_of_turn_event(faction, gs.round_number, order.index(faction) + 1, n)

    def _dry_run(self, action):
        """The events `action(sim_engine)` would log, run against a private
        copy of the engine so the real game is untouched. Only for phases with
        no dice (Capture, Deploy + Income)."""
        sim = copy.copy(self.engine)
        for key, value in self.engine.__dict__.items():
            if key in ('data', 'turn_log', 'stats'):
                continue
            setattr(sim, key, copy.deepcopy(value))
        sim.turn_log = TurnLog()
        sim.stats = None
        action(sim)
        return sim.turn_log.events

    # ---- helpers ---------------------------------------------------------

    def _diplomacy_block(self, faction):
        """The 'human' block of a Diplomacy queue."""
        engine = self.engine
        options = engine.legal_alliance_options(faction)
        # only those the alliance-size limit really lets it ask
        options['eligible_invite_targets'] = [t for t in options['eligible_invite_targets']
                                              if engine.can_invite_to_alliance(faction, t)]
        return {
            'kind': 'diplomacy',
            'members': sorted(engine.alliance_members(faction)),
            'options': {**options, 'alliance_action_used': engine.alliance_action_taken(faction)},
            'surrender': engine.legal_surrender_targets(faction),
            'game_would_end': engine.would_game_end(),
        }

    def _move_block(self, faction, kind):
        """The 'human' block of a move-phase queue: what is still legal per unit
        (with the staged moves applied) and the staged moves themselves."""
        return {
            'kind': kind,
            'options': self.engine.move_options_with_staged(faction, kind),
            'orders': self.engine.staged_moves_detail(faction, kind),
        }

    def _is_human(self, faction):
        return self.engine.game_state.factions[faction].mode == FactionMode.HUMAN

    def _check_all_bots(self):
        gs = self.engine.game_state
        for code in gs.active_factions():
            if code not in self.bots and not self._is_human(code):
                return f'{code} is neither a HUMAN nor a BOT with a bot attached'
        return None

    def _state_message(self):
        return {'type': 'state', 'game_state': self.engine.game_state.to_dict()}

    def _game_over_message(self):
        return {'type': 'game_over', 'report': build_game_report(self.engine, self.turn_log, self.bots)}

    @staticmethod
    def _error(message):
        return {'type': 'error', 'message': message}
