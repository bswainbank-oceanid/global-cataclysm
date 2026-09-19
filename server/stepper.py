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
    A Non-Combat Move whose faction has aircraft to send home is queued as
    two steps: first phase "RETURN_TO_BASE" (not a GameState phase; its
    events are the return_to_base flights the game makes automatically),
    then the ordinary "NONCOMBAT_MOVE" queue, planned once they have landed.
    {"type": "phase_result", "faction": ..., "phase": ..., "events": [...]}
        What executing that phase actually logged (for Combat Resolution:
        the roll-by-roll events and one battle_summary per battle).
    {"type": "state", "game_state": ...}   sent after every executed phase.
    {"type": "game_over"}
    {"type": "error", "message": ...}
"""
import copy

from engine.state import Phase
from engine.turn_log import TurnLog

_PHASES = list(Phase)

# A queue step of its own, not a GameState phase: the automatic return-to-base
# that opens Non-Combat Move (see PhaseStepper._plan_current_phase).
RETURN_TO_BASE = 'RETURN_TO_BASE'


class PhaseStepper:
    def __init__(self, engine, turn_log, bots):
        self.engine = engine
        self.turn_log = turn_log
        self.bots = bots
        self._queue = None  # the phase_queue message awaiting "next"
        self._skipped_before = []
        self._alliance_plan = None

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

    def next(self):
        problem = self._check_all_bots()
        if problem:
            return [self._error(problem)]
        gs = self.engine.game_state
        if gs.game_over:
            return [{'type': 'game_over'}]
        if self._queue is None:
            self._plan_current_phase()
        return self._execute_queued_phase()

    # ---- execute ---------------------------------------------------------

    def _execute_queued_phase(self):
        gs = self.engine.game_state
        faction, queued = self._queue['faction'], self._queue['phase']
        start = len(self.turn_log.events)
        messages = []
        if queued == RETURN_TO_BASE:
            # Its own step, ahead of the rest of Non-Combat Move: air units
            # that fought this turn fly home. GameState.phase doesn't move.
            self.engine.process_return_to_base(faction)
            phase = None
        else:
            phase = gs.phase
            self._commit(faction, phase)
        messages.append({'type': 'phase_result', 'faction': faction, 'phase': queued,
                         'events': self.turn_log.events[start:]})

        self._queue = None
        if phase is None:
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
        engine, bot = self.engine, self.bots[faction]
        active = faction in engine.game_state.active_factions()
        if phase == Phase.PURCHASE:
            engine.confirm_purchases(faction)
        elif phase == Phase.COMBAT_MOVE:
            engine.confirm_combat_moves(faction)
        elif phase == Phase.COMBAT_RESOLUTION:
            engine.resolve_combat(faction)
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
                bot.commit_alliance_phase(self._alliance_plan)
                engine.process_game_end_check(faction)
            else:
                engine.game_state.game_over = engine.would_game_end()

    # ---- plan ------------------------------------------------------------

    def _plan_current_phase(self):
        """Has the active bot decide the current phase and stage it (nothing
        is committed), then builds the phase_queue message describing it."""
        engine, gs = self.engine, self.engine.game_state
        faction, phase = gs.active_faction, gs.phase
        bot = self.bots[faction]

        if faction not in gs.active_factions():
            events = []  # eliminated during its own turn; nothing left to do (see _commit)
        elif phase == Phase.PURCHASE:
            bot.plan_purchase_phase()
            events = [engine.staged_purchase_event(faction)]
        elif phase == Phase.COMBAT_MOVE:
            bot.plan_combat_move_phase()
            events = [engine.staged_combat_move_event(faction)]
        elif phase == Phase.COMBAT_RESOLUTION:
            events = engine.battle_previews(faction)
        elif phase == Phase.NONCOMBAT_MOVE:
            if not engine.has_processed_return_to_base(faction):
                homeward = self._dry_run(lambda sim: sim.process_return_to_base(faction))
                if homeward:
                    # Queue the flight home on its own first; the rest of the
                    # phase is planned once it has run (from the new positions).
                    self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': RETURN_TO_BASE,
                                   'events': homeward, 'skipped': [p.value for p in self._skipped_before]}
                    return
            bot.plan_noncombat_move_phase()
            events = [engine.staged_noncombat_move_event(faction)]
        elif phase == Phase.CAPTURE:
            events = self._dry_run(lambda sim: (
                sim.process_capture_territory(faction), sim.process_elimination_check()))
        elif phase == Phase.DEPLOY_INCOME:
            events = self._dry_run(lambda sim: sim.deploy_and_collect_income(faction))
        else:  # ALLIANCES
            self._alliance_plan = bot.plan_alliance_phase()
            events = [{'kind': 'alliance_plan', 'faction': faction, **self._alliance_plan}]

        self._queue = {'type': 'phase_queue', 'faction': faction, 'phase': phase.value,
                       'events': events, 'skipped': [p.value for p in self._skipped_before]}

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

    def _check_all_bots(self):
        gs = self.engine.game_state
        for code in gs.active_factions():
            if code not in self.bots:
                return f'watch mode needs every active faction to be a BOT; {code} is not'
        return None

    def _state_message(self):
        return {'type': 'state', 'game_state': self.engine.game_state.to_dict()}

    @staticmethod
    def _error(message):
        return {'type': 'error', 'message': message}
