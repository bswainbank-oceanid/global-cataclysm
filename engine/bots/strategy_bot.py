"""
StrategyBot: the heuristic bot. It has the RandomBot's interface (so the driver, the watcher's stepper and
the human UI's staging all work unchanged) and inherits its alliance-phase logic -- the invite / accept /
withdraw policy is untouched -- but decides purchases and moves by planning the turn (bots/planner.py):

  * at the start of the game it draws a strategy style from its faction's weights (Strategic, Defensive,
    Expansive, Controlling, or Variable, which re-draws among the four every turn by its own weights);
  * at Purchase it plans the whole turn, buys what the plan needs (each unit drawn by the faction's unit
    odds), and remembers the combat moves;
  * at Combat Move it plays the remembered combat moves;
  * at Non-Combat Move, once combat has resolved, it plans again from the board as it now stands and moves.

RandomBot stays available as the baseline; see server/lobby.py's per-seat bot AI.
"""
import copy

from ..engine import CombatMoveOrder, NonCombatMoveOrder
from .planner import DEFAULT_BUDGET, Planner
from .random_bot import RandomBot
from .strategy_settings import load_settings


class StrategyBot(RandomBot):
    def __init__(self, engine, faction, rng=None, settings=None, budget=DEFAULT_BUDGET):
        super().__init__(engine, faction, rng)
        self.settings = settings or load_settings(engine.data)
        self.budget = budget  # planning effort per pass, in simulated battles (not seconds: seeded games replay exactly)
        self.base_style = self.settings.draw_style(faction, self.rng)
        self.style = self.base_style  # the concrete style this turn (differs from base only when Variable)
        self._plan = None
        self._plan_turn = None
        self.last_plan = None  # the latest Plan, for inspection and the arena's reports

    # ---- Purchase (and the plan for the whole turn) ---------------------------------------------------

    def plan_purchase_phase(self):
        gs = self.engine.game_state
        if self._plan_turn != gs.global_turn:  # a watcher may ask twice: plan once per turn
            self._maybe_reroll_variable_alliance_settings()
            self._maybe_roll_treacherous_intent()
            self.style = self.settings.turn_style(self.faction, self.base_style, self.rng)
            planner = Planner(self.engine, self.faction, self.settings, self.style, self.rng, 'full', self.budget)
            self._plan = planner.run(treasonous=False)
            self._plan_turn = gs.global_turn
            self.last_plan = self._plan
        self._stage_purchases(self._plan.purchases)

    def _stage_purchases(self, orders):
        try:
            self.engine.submit_purchases(self.faction, orders)
            return
        except ValueError:
            pass
        accepted = []
        for order in orders:
            try:
                self.engine.submit_purchases(self.faction, accepted + [order])
                accepted.append(order)
            except ValueError:
                continue
        self.engine.submit_purchases(self.faction, accepted)

    # ---- Combat Move -------------------------------------------------------------------------------

    def plan_combat_move_phase(self):
        gs = self.engine.game_state
        if self._plan_turn != gs.global_turn:  # no purchase phase ran for this turn (e.g. a driver that skips it)
            self.plan_purchase_phase_silently()
        working = copy.deepcopy(gs)
        orders = []
        for order in self._plan.combat:
            try:
                self.engine._execute_combat_moves([order], self.faction, working)
            except ValueError:
                continue
            orders.append(order)
        self._stage_incrementally(orders, self.engine.submit_combat_moves)

    def plan_purchase_phase_silently(self):
        """Plan the turn without staging purchases (the Purchase phase has already passed)."""
        gs = self.engine.game_state
        self.style = self.settings.turn_style(self.faction, self.base_style, self.rng)
        planner = Planner(self.engine, self.faction, self.settings, self.style, self.rng, 'full', self.budget)
        planner.allow_purchase = False
        self._plan = planner.run(treasonous=False)
        self._plan_turn = gs.global_turn
        self.last_plan = self._plan

    # ---- Non-Combat Move -----------------------------------------------------------------------------

    def plan_noncombat_move_phase(self):
        if not self.engine.has_processed_return_to_base(self.faction):
            self.engine.process_return_to_base(self.faction)
        gs = self.engine.game_state
        treasonous = bool(gs.factions[self.faction].pending_treacherous_withdrawal) and gs.factions[self.faction].alliance is not None
        planner = Planner(self.engine, self.faction, self.settings, self.style, self.rng, 'noncombat', self.budget)
        plan = planner.run(treasonous=treasonous)
        self.last_plan = plan
        working = copy.deepcopy(gs)
        orders = []
        for order in plan.noncombat:
            try:
                self.engine._execute_noncombat_moves([order], self.faction, working)
            except ValueError:
                continue
            orders.append(order)
        self._stage_incrementally(orders, self.engine.submit_noncombat_moves)
