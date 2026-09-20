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
    {"type": "stage_alliance", "action": "none"|"invite"|"withdraw", "target": "UE"}
                        a HUMAN faction's Alliances phase: choose its one optional
                        action (invite a faction / withdraw from its alliance /
                        nothing). Checked by a dry run of the engine call, so an
                        illegal pick (e.g. it would exceed the alliance size limit)
                        is an "error" plus the unchanged queue.
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
and staged moves; its Alliances queue carries {kind: "alliance", members, options
(eligible_invite_targets, can_withdraw), staged (the chosen action),
game_would_end}. Its Capture and Deploy phases are automatic.

A bot's Alliances phase that INVITES a human faction cannot be resolved by policy:
its phase_queue carries an "invitation" {from, to, members, answered, accepts},
the human answers with respond_invitation, the queue is re-sent with the answer
(shown in its alliance_plan event), and only then will "next" execute it.

Server -> client (always broadcast to watchers):
    {"type": "phase_queue", "faction": "AAC", "phase": "PURCHASE",
     "events": [...], "skipped": ["COMBAT_MOVE"]}
        What `faction` WILL do this phase, in turn_log's event shapes
        (purchase / combat_move / noncombat_move / alliance_plan), plus
        'battle_preview' (the battles about to be fought and who is in
        them) for Combat Resolution, or -- for the automatic Capture and
        Deploy + Income phases -- the events a dry run of the phase
        produces. `skipped`: phases the game passed over just before this
        one (e.g. Combat Move on a faction's first turn).
    Every faction's turn opens with a phase "START_OF_TURN" (also not a
    GameState phase): its one event, start_of_turn {faction, round, turn,
    turns_in_round}, just says which round and which turn is starting;
    executing it changes nothing but moves on to that faction's Purchase.
    A Non-Combat Move whose faction has aircraft to send home is queued as
    two steps: first phase "RETURN_TO_BASE" (not a GameState phase; its
    events are the return_to_base flights the game makes automatically),
    then the ordinary "NONCOMBAT_MOVE" queue, planned once they have landed.
    Combat Resolution is queued one battle at a time: each phase_queue holds
    that battle's battle_preview and a "battle": {"index": i, "count": n};
    "next" fights it, and the phase stays Combat Resolution until the last.
    {"type": "phase_result", "faction": ..., "phase": ..., "events": [...]}
        What executing that phase actually logged (for Combat Resolution:
        the roll-by-roll events and one battle_summary per battle).
    {"type": "state", "game_state": ...}   sent after every executed phase.
    {"type": "game_over"}
    {"type": "error", "message": ...}
"""
import copy

from engine.bots.alliance_policy import accepts_invite
from engine.engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from engine.state import FactionMode, Phase
from engine.turn_log import TurnLog

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
        self._alliance_plan = None
        self._battles = None       # the current Combat Resolution's [(territory_id, battle_type), ...]
        self._battle_index = 0     # which of them is queued now
        self._alliance_plan_for = None  # (faction, global_turn) the current _alliance_plan belongs to
        self._invitation = None    # a bot's invitation to a human awaiting its answer: {from, to, answered}
        self._turn_announced = None  # (faction, global_turn) whose Start of Turn has been executed

    # ---- protocol entry points -------------------------------------------

    def watch(self):
        """Current state, plus the queue awaiting execution (planned now if
        this is the very first join)."""
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        gs = self.engine.game_state
        if gs.game_over:
            return [self._state_message(), {'type': 'game_over'}]
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

    def stage_alliance(self, faction, action, target):
        """A human's one Alliances-phase choice."""
        gs = self.engine.game_state
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        if self._queue is None:
            self._plan_current_phase()
        if faction not in gs.factions or not self._is_human(faction):
            return [self._error(f'{faction} is not a human-controlled faction')]
        if faction != gs.active_faction or gs.phase != Phase.ALLIANCES or self._queue['phase'] != Phase.ALLIANCES.value:
            return [self._error(f"it is not {faction}'s Alliances phase")]
        if action not in ('none', 'invite', 'withdraw'):
            return [self._error(f'unknown alliance action {action!r}'), self._queue]
        plan = {'action': action}
        try:
            if action == 'invite':
                if not target:
                    raise ValueError("'invite' needs a target")
                if target not in self.engine.legal_alliance_options(faction)['eligible_invite_targets']:
                    raise ValueError(f'{target} is not a legal invite target for {faction} right now')
                self._dry_run(lambda sim: sim.invite_to_alliance(faction, target, True))  # would it be legal to accept?
                plan['target'] = target
            elif action == 'withdraw':
                self._dry_run(lambda sim: sim.withdraw_from_alliance(faction))
        except ValueError as e:
            return [self._error(str(e)), self._queue]
        self._alliance_plan = plan
        self._alliance_plan_for = self._alliance_key(faction)
        self._plan_current_phase()
        return [self._queue]

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
            return [{'type': 'game_over'}]
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
            self.engine.process_return_to_base(faction)
            phase = None
        else:
            phase = gs.phase
            stay = self._commit(faction, phase)
        messages.append({'type': 'phase_result', 'faction': faction, 'phase': queued,
                         'events': self.turn_log.events[start:]})

        self._queue = None
        if phase == Phase.ALLIANCES:
            self._alliance_plan = None
            self._alliance_plan_for = None
            self._invitation = None
        if phase is None or stay:
            self._skipped_before = []
        elif phase == Phase.ALLIANCES:
            if gs.game_over:
                return messages + [self._state_message(), {'type': 'game_over'}]
            self.engine.advance_turn()
            self._skipped_before = []
        else:
            before = _PHASES.index(phase)
            self.engine.advance_phase()
            self._skipped_before = _PHASES[before + 1:_PHASES.index(gs.phase)]
        self._plan_current_phase()
        return messages + [self._queue, self._state_message()]

    def _commit(self, faction, phase):
        """Executes the queued phase. A faction can be eliminated during its
        OWN turn (its territory handed to an ally at Capture drops it to <=1
        Strategic Center): active_factions() then excludes it and every
        remaining phase call for it would raise, so those become no-ops --
        except the global elimination check and the game-over check, which
        still run (same handling as engine.bots.driver)."""
        engine, bot = self.engine, self.bots.get(faction)
        active = faction in engine.game_state.active_factions()
        if phase == Phase.PURCHASE:
            engine.confirm_purchases(faction)
        elif phase == Phase.COMBAT_MOVE:
            engine.confirm_combat_moves(faction)
        elif phase == Phase.COMBAT_RESOLUTION:
            return self._commit_one_battle(faction)
        elif phase == Phase.NONCOMBAT_MOVE:
            engine.confirm_noncombat_moves(faction)
        elif phase == Phase.CAPTURE:
            if active:
                engine.process_capture_territory(faction)
            engine.process_elimination_check()
        elif phase == Phase.DEPLOY_INCOME:
            if active:
                engine.deploy_and_collect_income(faction)
        elif phase == Phase.ALLIANCES:
            if active:
                self._apply_alliance(faction, bot)
                engine.process_game_end_check(faction)
            else:
                engine.game_state.game_over = engine.would_game_end()
        return False

    def _apply_alliance(self, faction, bot):
        """Executes the queued Alliances action: a bot's through its own commit, a
        human's staged choice directly. An invite to a BOT is decided by that bot's
        own policy at this moment; one to the HUMAN uses their answer."""
        plan = self._alliance_plan or {'action': 'none'}
        if bot is not None:
            bot.commit_alliance_phase(plan)
            return
        try:
            if plan['action'] == 'invite':
                accepts = plan.get('accepts')
                if accepts is None:
                    accepts = accepts_invite(self.engine, plan['target'], faction)
                self.engine.invite_to_alliance(faction, plan['target'], accepts)
            elif plan['action'] == 'withdraw':
                self.engine.withdraw_from_alliance(faction)
        except ValueError:
            pass  # no longer legal by the time it runs: the turn simply ends with no action, as for a bot

    def _alliance_key(self, faction):
        return (faction, self.engine.game_state.global_turn)

    def _commit_one_battle(self, faction):
        """Combat Resolution goes battle by battle (each its own queue step, so
        a watcher can pause between them): fights the queued one. Returns True
        while more battles remain in the phase. With none declared, just runs
        the (empty) resolution so the phase is marked done."""
        engine = self.engine
        if not self._battles:
            engine.resolve_combat(faction)
            self._battles = None
            return False
        if self._battle_index == 0:
            engine.begin_combat_resolution(faction)
        territory_id, battle_type = self._battles[self._battle_index]
        engine.resolve_one_battle(faction, territory_id, battle_type)
        self._battle_index += 1
        if self._battle_index < len(self._battles):
            return True
        self._battles = None
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
            if self._battles is None:
                self._battles = engine.declared_battles(faction)
                self._battle_index = 0
            events = []
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
            events = self._dry_run(lambda sim: (
                sim.process_capture_territory(faction), sim.process_elimination_check()))
        elif phase == Phase.DEPLOY_INCOME:
            events = self._dry_run(lambda sim: sim.deploy_and_collect_income(faction))
        else:  # ALLIANCES
            key = self._alliance_key(faction)
            if self._alliance_plan_for != key or self._alliance_plan is None:
                # A fresh plan for this turn (re-planning after a staging/answer must
                # keep the one already made -- a bot's pick is random).
                self._alliance_plan = {'action': 'none'} if human else bot.plan_alliance_phase()
                self._alliance_plan_for = key
                self._invitation = None
                target = self._alliance_plan.get('target')
                if not human and self._alliance_plan['action'] == 'invite' and self._is_human(target):
                    self._alliance_plan['accepts'] = None  # only the player can say
                    self._invitation = {'from': faction, 'to': target, 'answered': False}
            events = [{'kind': 'alliance_plan', 'faction': faction, **self._alliance_plan}]
            if human:
                extra['human'] = self._alliance_block(faction)
            if self._invitation is not None:
                members = sorted(engine.alliance_members(faction))
                extra['invitation'] = {**self._invitation, 'members': members,
                                       'accepts': self._alliance_plan.get('accepts')}

        self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': phase.value,
                       'events': events, 'skipped': [p.value for p in self._skipped_before], **extra}
        if phase == Phase.COMBAT_RESOLUTION and self._battles:
            self._queue['battle'] = {'index': self._battle_index, 'count': len(self._battles)}

    def _turn_key(self, faction):
        return (faction, self.engine.game_state.global_turn)

    def _start_of_turn_event(self, faction):
        """Round and turn, in the client's own terms: a round is one turn for each
        faction still in play, and the turn is this faction's place in it."""
        gs = self.engine.game_state
        order = gs.active_factions()
        n = max(1, len(order))
        return TurnLog.start_of_turn_event(faction, gs.global_turn // n + 1, order.index(faction) + 1, n)

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

    def _alliance_block(self, faction):
        """The 'human' block of an Alliances queue."""
        engine = self.engine
        return {
            'kind': 'alliance',
            'members': sorted(engine.alliance_members(faction)),
            'options': engine.legal_alliance_options(faction),
            'staged': dict(self._alliance_plan or {'action': 'none'}),
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

    @staticmethod
    def _error(message):
        return {'type': 'error', 'message': message}
