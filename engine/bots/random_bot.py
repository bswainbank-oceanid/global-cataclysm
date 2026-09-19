"""
A simple, deliberately unsophisticated bot -- queries GameEngine for
legal orders and picks among them with a fixed, documented policy (no
learning, no lookahead), through the exact same phased public API a
human caller would use. Purpose is to exercise the full turn loop
end-to-end across many turns as an integration/stress test, not to be a
good opponent (docs/GAME_ARCHITECTURE.md build plan, Step 5).

Behavior, as specified by the user this session:
- Purchase: spend a random amount up to 55% of the treasury at
  Strategic-Center-associated targets -- a land SC itself, or a sea zone
  whose purchase would draw cost/capacity from an adjacent owned SC
  (confirmed: include adjacent sea zones too) -- then spend whatever's
  left randomly at every other legal target. Both passes repeatedly pick
  a random purchasable unit type and a random target, keeping the pick
  only if the growing order list stays legal (GameEngine.submit_purchases
  is the source of truth) and, for the SC pass, within its sub-budget;
  each pass gives up after a run of consecutive rejections.
  policy.excluded_naval_purchase_zones (the landlocked Caspian Sea, id
  43 -- setup.excluded_naval_zones) is never a candidate target at all,
  for either pass -- a shared rule for this bot AND ANY FUTURE BOT in
  this package, not something specific to RandomBot's own policy. The
  candidate unit type for a given attempt is always chosen AFTER the
  target, and restricted to what that target actually is -- a sea
  target's pool never includes a Land-category unit type at all ("SCs
  should never produce land units in sea areas," a bot defense policy,
  this session -- GameEngine itself still allows it; deploying a land
  unit to a sea zone is a deliberate, valid feature, just not one this
  bot chooses to use).
- Combat Move: for each of the bot's own units that hasn't combat-moved
  yet, make the first legal combat move found. "First" is defined here
  as closest (fewest hops), ties broken by lowest territory_id -- a
  fixed, reproducible order, since the spec doesn't define "first" more
  precisely than that. The bot never retreats from a contested area via
  combat move -- in this engine that's only ever possible for a land
  unit mid-transit through hostile/contested water (movement.py's
  amphibious STOP_AND_PASS_LAND_ONLY exception is the sole way a combat
  move can legally end on safe, friendly territory; every other legal
  stop is already an attack, a capture, or joining a fight). For such a
  unit, the preference order is: an amphibious ATTACK (a land
  destination that's actually an attack/capture/join, not just a safe
  landing) first, falling back to merely getting onto allied land only
  if no attack option exists this turn, falling back further to simply
  joining the naval battle in the sea zone itself only if no land option
  exists at all ("transported land units should seek land conflicts,"
  always trying to combat-move OUT of a sea battle rather than settling
  for it). Sea units have no retreat option to begin with -- every legal
  combat-move stop for one is already an attack or joining one (open,
  uncontested water is never itself a legal stop), so any legal
  destination already fights to the end by construction.
- Non-Combat Move: for each of the bot's own units that hasn't moved
  this turn, move toward the nearest enemy-OWNED land territory (read as
  ownership, matching how the rest of the ruleset uses "enemy
  territory") -- among the unit's own legal non-combat destinations,
  pick whichever minimizes remaining graph distance
  (movement.graph_distances -- plain adjacency, not move-budget-aware)
  to that target. A unit with no legal moves, or no enemy territory
  anywhere on the reachable graph, simply stays put.
- Defense: an Infantry unit currently sitting on a Strategic Center this
  faction owns never moves at all, via combat move OR non-combat move --
  it garrisons there permanently once it arrives, however it got there
  (bot defense policy, this session: "Infantry should never leave an
  SC... once they reach an SC, they just stay there"). Checked fresh
  every phase from the unit's current position (_garrisons_an_sc), so a
  later arrival is covered automatically without any separate
  "has arrived" state.
- Alliances: one optional invite/withdraw per turn, driven entirely by
  FactionState.alliance_strategy/alliance_behavior (game-start settings,
  see setup.build_game_state's alliance_strategies/alliance_behaviors
  params) -- the actual decision logic lives in engine.bots.
  alliance_policy, a standalone module, since accepting an invitation is
  the TARGET faction's own strategy decision, not the inviter's, and
  needs to be computable for any faction without needing its bot
  instance. A 'variable' strategy/behavior (added this session) re-rolls
  its own CONCRETE pick every turn -- see take_alliance_phase/
  _maybe_reroll_variable_alliance_settings/_maybe_roll_treacherous_intent
  below and alliance_policy's module docstring for the per-strategy/
  behavior rules themselves.

Only Purchase is randomized (per spec); Combat Move and Non-Combat Move
are both deterministic given the board state, which keeps a driven game
reproducible for a given purchase RNG seed. Alliance decisions are a mix:
target/withdrawal SELECTION among several equally-valid options is
randomized (this bot's own rng), but WHETHER to act at all follows each
strategy/behavior's fixed rule.
"""
import copy
import random

from ..engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from ..movement import (
    _is_ally_or_self, graph_distances, legal_air_move_destinations,
    legal_combat_move_paths, legal_noncombat_move_paths, trace_combat_move,
)
from ..state import FactionMode
from . import alliance_policy
from .policy import excluded_naval_purchase_zones


class RandomBot:
    def __init__(self, engine, faction, rng=None):
        self.engine = engine
        self.faction = faction
        self.rng = rng or random.Random()

    # ---- Purchase -----------------------------------------------------

    def take_purchase_phase(self):
        self.plan_purchase_phase()
        self.engine.confirm_purchases(self.faction)

    def plan_purchase_phase(self):
        """Decides and STAGES this phase's purchases (engine.submit_purchases)
        without committing them -- the staged list is what a watcher shows
        as queued; engine.confirm_purchases executes it. take_purchase_phase
        is plan + confirm back to back."""
        self._maybe_reroll_variable_alliance_settings()
        self._maybe_roll_treacherous_intent()
        gs = self.engine.game_state
        treasury = gs.factions[self.faction].treasury_mpc

        sc_targets, other_targets = self._purchase_target_pools()
        orders = []
        if sc_targets:
            sc_budget = self.rng.uniform(0, 0.55) * treasury
            orders = self._random_fill_purchases(orders, sc_targets, sc_budget)
        if other_targets:
            orders = self._random_fill_purchases(orders, other_targets, treasury)

        self.engine.submit_purchases(self.faction, orders)

    def _purchase_target_pools(self):
        """(sc_targets, other_targets): GameEngine.legal_purchase_targets
        (moved there this session -- any caller, not just a bot, can use
        it now), with policy.excluded_naval_purchase_zones dropped from
        both pools -- every bot's purchase policy, not just this one's
        (the engine itself still allows purchasing there; see that
        function's own docstring)."""
        sc_targets, other_targets = self.engine.legal_purchase_targets(self.faction)
        excluded = excluded_naval_purchase_zones(self.engine.data)
        sc_targets = set(sc_targets) - excluded
        other_targets = set(other_targets) - excluded
        return list(sc_targets), list(other_targets)

    def _random_fill_purchases(self, orders, targets, budget_cap, max_consecutive_failures=25):
        """Picks the target FIRST, then a unit type valid for THAT
        target -- never the reverse -- so a sea target's candidate pool
        never includes a Land-category unit type at all (bot defense
        policy, this session: 'SCs should never produce land units in
        sea areas' -- and this applies to every sea target, not just an
        SC-funded one, since the underlying issue -- a land unit with
        nothing to independently exist on in open water -- is the same
        either way). GameEngine itself still allows it (confirmed
        deliberate and tested: a land unit purchased at a sea zone just
        starts out already mid-transit, no different from one that
        walked into hostile water and got swept up as cargo) -- this is
        a bot-policy choice, not an engine-level restriction."""
        terrs = self.engine.data.territories()
        unit_defs = self.engine.data.units()
        failures = 0
        while failures < max_consecutive_failures:
            target = self.rng.choice(targets)
            is_sea = terrs[target]['type'] == 'sea'
            candidate_types = [
                t for t, d in unit_defs.items()
                if d.get('purchasable') and not (is_sea and d['category'] == 'Land')
            ]
            if not candidate_types:
                failures += 1
                continue
            unit_type = self.rng.choice(candidate_types)
            candidate = self._with_extra_unit(orders, unit_type, target)
            try:
                total_cost = self.engine.submit_purchases(self.faction, candidate)
            except ValueError:
                failures += 1
                continue
            if total_cost > budget_cap:
                failures += 1
                continue
            orders = candidate
            failures = 0
        return orders

    @staticmethod
    def _with_extra_unit(orders, unit_type, target):
        for i, o in enumerate(orders):
            if o.unit_type == unit_type and o.deploy_at == target:
                new_orders = list(orders)
                new_orders[i] = PurchaseOrder(unit_type, o.qty + 1, target)
                return new_orders
        return orders + [PurchaseOrder(unit_type, 1, target)]

    # ---- shared defense policy --------------------------------------------

    def _garrisons_an_sc(self, unit, origin_id, game_state):
        """True for an Infantry unit currently sitting on a Strategic
        Center this faction owns -- bot defense policy, this session:
        Infantry never leaves an SC, and once one arrives there (however
        it got there) it just stays, permanently -- checked fresh every
        phase from the unit's CURRENT position, so no separate
        "has arrived" state is needed; it's already implied by being
        there. Scoped to Infantry specifically -- the ruleset's
        designated defensive garrison unit type (see purchase.
        contested_land_deploy_restriction, the only unit type ever
        allowed into a contested territory) -- not other land units."""
        if unit.unit_type != 'Infantry':
            return False
        terr = self.engine.data.territories().get(origin_id)
        if terr is None or terr['type'] != 'land' or not terr.get('strategic_center'):
            return False
        return game_state.territories[origin_id].owner == self.faction

    # ---- Combat Move ----------------------------------------------------

    def take_combat_move_phase(self):
        self.plan_combat_move_phase()
        self.engine.confirm_combat_moves(self.faction)

    def plan_combat_move_phase(self):
        """Decides and STAGES this phase's combat moves (confirm_combat_moves
        commits them; take_combat_move_phase is plan + confirm). Plans against a single working copy of the game state, applying
        each accepted order to it immediately (via GameEngine's own
        _execute_combat_moves -- the same primitive submit_combat_moves
        uses to validate) before deciding the next unit's move -- so a
        later unit's "first legal move" search already sees the
        consequences of an earlier one (a territory it just entered and
        marked contested, a rider a carrier's own move already swept
        away), instead of being computed against a stale snapshot and
        then possibly conflicting at submission time. This keeps the
        whole phase to ONE deep copy here plus GameEngine's own single
        validating deep copy inside the final submit_combat_moves call
        -- not one deep copy per unit (submit_combat_moves' validation
        is O(whole board state) per call, so that difference matters a
        lot once a game has run a while and the board has grown)."""
        working = copy.deepcopy(self.engine.game_state)
        unit_defs = self.engine.data.units()
        orders = []

        for tid in list(working.territories.keys()):
            for u in list(working.territories[tid].units):
                if u.owner != self.faction or u.has_moved_combat:
                    continue
                if self._garrisons_an_sc(u, tid, working):
                    continue
                category = unit_defs[u.unit_type]['category']
                path = self._first_combat_move_path(u, tid, category, working)
                if path is None:
                    continue
                order = CombatMoveOrder(u.unit_id, path)
                try:
                    self.engine._execute_combat_moves([order], self.faction, working)
                except ValueError:
                    continue
                orders.append(order)

        self._stage_incrementally(orders, self.engine.submit_combat_moves)

    def _first_combat_move_path(self, unit, origin_id, category, game_state):
        """The bot never retreats from a contested area via combat move
        -- in practice this only ever comes up for a land unit mid-transit
        through hostile/contested water (movement.py's amphibious
        STOP_AND_PASS_LAND_ONLY exception is the ONLY way a combat move
        can ever legally end on safe, friendly territory at all; every
        other legal combat-move stop is inherently an attack, a capture,
        or joining a fight already in progress, by construction). For a
        land unit in that situation, prefer an amphibious ATTACK (a land
        destination classified 'attack'/'capture'/'join_contest') over
        merely landing safely on allied territory to escape the water --
        the safe landing is only ever the fallback when no attack option
        exists this turn -- and prefer any land destination at all over
        just stopping in the hostile sea zone itself (always try to
        combat move OUT of a sea battle)."""
        if category == 'Air':
            legal = legal_air_move_destinations(
                unit.unit_type, self.faction, origin_id, 'combat', game_state, self.engine.data,
            )
            if not legal:
                return None
            return [origin_id, min(legal)]

        paths = legal_combat_move_paths(unit.unit_type, self.faction, origin_id, game_state, self.engine.data)
        if not paths:
            return None

        if category != 'Land':
            # Sea units never have a retreat option to begin with -- every
            # legal combat-move stop for a naval unit is already an attack
            # or joining one (open, uncontested water is never itself a
            # legal stop) -- so any legal destination already fights to
            # the end by construction.
            dest = min(paths, key=lambda d: (len(paths[d]), d))
            return paths[dest]

        terrs = self.engine.data.territories()
        attack_dests, safe_landing_dests, sea_dests = [], [], []
        for dest_id, path in paths.items():
            if terrs[dest_id]['type'] != 'land':
                sea_dests.append(dest_id)
                continue
            trace = trace_combat_move(unit.unit_type, self.faction, path, game_state, self.engine.data)
            (safe_landing_dests if trace.final_kind == 'safe_landing' else attack_dests).append(dest_id)

        candidates = attack_dests or safe_landing_dests or sea_dests
        dest = min(candidates, key=lambda d: (len(paths[d]), d))
        return paths[dest]

    # ---- Non-Combat Move ------------------------------------------------

    def take_noncombat_move_phase(self):
        self.plan_noncombat_move_phase()
        self.engine.confirm_noncombat_moves(self.faction)

    def plan_noncombat_move_phase(self):
        """Same single-working-copy planning approach as
        plan_combat_move_phase, for the same reason (avoid an O(units)
        number of whole-board deep copies). Stages only; confirm_
        noncombat_moves commits."""
        # Mutates the real game state, so it must run before the copy below -- unless a
        # watcher already ran it as its own step (server/stepper.py).
        if not self.engine.has_processed_return_to_base(self.faction):
            self.engine.process_return_to_base(self.faction)
        working = copy.deepcopy(self.engine.game_state)
        unit_defs = self.engine.data.units()
        orders = []

        for tid in list(working.territories.keys()):
            for u in list(working.territories[tid].units):
                if u.owner != self.faction or u.has_moved_noncombat:
                    continue
                if self._garrisons_an_sc(u, tid, working):
                    continue
                category = unit_defs[u.unit_type]['category']
                if category != 'Air' and u.has_moved_combat:
                    continue  # land/sea: combat move or non-combat move, never both this turn
                dest = self._advance_toward_nearest_enemy(u, tid, category, working)
                if dest is None:
                    continue
                order = NonCombatMoveOrder(u.unit_id, dest)
                try:
                    self.engine._execute_noncombat_moves([order], self.faction, working)
                except ValueError:
                    continue
                orders.append(order)

        self._stage_incrementally(orders, self.engine.submit_noncombat_moves)

    def _advance_toward_nearest_enemy(self, unit, origin_id, category, game_state):
        data = self.engine.data
        if category == 'Air':
            legal = legal_air_move_destinations(unit.unit_type, self.faction, origin_id, 'noncombat', game_state, data)
        else:
            legal = set(legal_noncombat_move_paths(unit.unit_type, self.faction, origin_id, game_state, data))
        if not legal:
            return None

        dist_from_origin = graph_distances(origin_id, data)
        terrs = data.territories()
        enemy_territories = [
            tid for tid, t in game_state.territories.items()
            if terrs[tid]['type'] == 'land' and t.owner is not None
            and game_state.factions[t.owner].mode != FactionMode.NEUTRAL
            and not _is_ally_or_self(game_state, self.faction, t.owner)
            and tid in dist_from_origin
        ]
        if not enemy_territories:
            return None
        target = min(enemy_territories, key=lambda tid: (dist_from_origin[tid], tid))

        dist_from_target = graph_distances(target, data)
        return min(legal, key=lambda d: (dist_from_target.get(d, float('inf')), d))

    # ---- Alliances --------------------------------------------------------

    def _maybe_reroll_variable_alliance_settings(self):
        """Start-of-turn hook (called from take_purchase_phase, BEFORE
        _maybe_roll_treacherous_intent -- order matters, see there):
        alliance_strategy/alliance_behavior == 'variable' each re-roll to
        a fresh concrete pick (engine.bots.alliance_policy.
        reroll_alliance_strategy/reroll_alliance_behavior, using this
        bot's own seeded rng), stored on FactionState.current_alliance_
        strategy/current_alliance_behavior -- 'variable' itself stays on
        alliance_strategy/alliance_behavior all game, same as any other
        resolved choice; only the concrete value actually driving this
        turn's decisions changes. A no-op for a non-'variable' bot."""
        fstate = self.engine.game_state.factions[self.faction]
        if fstate.alliance_strategy == 'variable':
            fstate.current_alliance_strategy = alliance_policy.reroll_alliance_strategy(self.rng)
        if fstate.alliance_behavior == 'variable':
            fstate.current_alliance_behavior = alliance_policy.reroll_alliance_behavior(self.rng)

    def _maybe_roll_treacherous_intent(self):
        """Start-of-turn hook (called from take_purchase_phase, Purchase
        always being the first phase of every turn -- see turn_order):
        alliance_behavior == 'treacherous' rolls its 15% withdraw chance
        here, using this bot's own seeded rng, and stashes the result on
        FactionState.pending_treacherous_withdrawal for
        alliance_policy.should_withdraw to read later this same turn, at
        the Alliances phase. Only rolls while actually in an alliance --
        nothing to withdraw from otherwise, so the decision is moot.
        Reads the EFFECTIVE behavior (alliance_policy.effective_alliance_
        behavior), not the raw field, so a 'variable' bot whose re-roll
        this same turn (see _maybe_reroll_variable_alliance_settings,
        called first) landed on 'treacherous' rolls this chance too --
        it's acting as that concrete behavior for the turn, same as if it
        had been fixed that way from the start."""
        gs = self.engine.game_state
        fstate = gs.factions[self.faction]
        if alliance_policy.effective_alliance_behavior(gs, self.faction) == 'treacherous' and fstate.alliance is not None:
            fstate.pending_treacherous_withdrawal = self.rng.random() < 0.15

    def take_alliance_phase(self):
        self.commit_alliance_phase(self.plan_alliance_phase())

    def plan_alliance_phase(self):
        """One optional action this turn, per alliance_strategy/
        alliance_behavior (engine.bots.alliance_policy) -- withdrawing
        (behavior-driven) takes priority over inviting (strategy-driven)
        when both would apply, since a bot that wants out this turn has
        no business also trying to grow the alliance it's about to
        leave. GameEngine re-validates everything; a ValueError here just
        means this bot's advisory pick didn't hold up (e.g. another
        faction's invite this same Alliances-phase pass already changed
        something) -- silently do nothing that turn rather than treat it
        as a bug, exactly like every other speculative bot decision in
        this file.

        Returns the chosen action WITHOUT executing it -- {'action':
        'withdraw'} / {'action': 'invite', 'target': code, 'accepts': bool}
        / {'action': 'none'} -- for commit_alliance_phase (a watcher shows
        it as queued first). take_alliance_phase is plan + commit."""
        gs = self.engine.game_state
        fstate = gs.factions[self.faction]

        if fstate.alliance is not None and gs.can_withdraw_from_alliances:
            if alliance_policy.should_withdraw(self.engine, self.faction):
                return {'action': 'withdraw'}

        target = alliance_policy.choose_invite_target(self.engine, self.faction, self.rng)
        if target is None:
            return {'action': 'none'}
        accepts = alliance_policy.accepts_invite(self.engine, target, self.faction)
        return {'action': 'invite', 'target': target, 'accepts': accepts}

    def commit_alliance_phase(self, plan):
        """Executes a plan_alliance_phase() result. A ValueError from the
        engine (the advisory pick no longer holds up) silently does
        nothing, as it always did."""
        try:
            if plan['action'] == 'withdraw':
                self.engine.withdraw_from_alliance(self.faction)
            elif plan['action'] == 'invite':
                self.engine.invite_to_alliance(self.faction, plan['target'], plan['accepts'])
        except ValueError:
            pass

    # ---- shared submission helper ---------------------------------------

    def _stage_incrementally(self, orders, submit_fn):
        """Stages the candidate list (engine.submit_*; confirming is the
        caller's job) -- the whole list in one call first, the fast, common
        path, since every order here was already checked for legality in
        isolation when picked, so the whole batch is usually legal together
        too. submit_combat_moves/submit_noncombat_moves each deep-copy the
        entire GameState to validate, so this matters: falling back to
        accepting one order at a time (dropping whichever turn out illegal
        once earlier ones in the same batch are applied -- e.g. two units
        both wanting to move through the same just-captured territory) is
        only worth its O(n) extra deep-copies in the rare case the whole
        batch doesn't already work. Always stages, even an empty list."""
        try:
            submit_fn(self.faction, orders)
            return
        except ValueError:
            pass

        accepted = []
        for order in orders:
            candidate = accepted + [order]
            try:
                submit_fn(self.faction, candidate)
            except ValueError:
                continue
            accepted = candidate
        submit_fn(self.faction, accepted)
