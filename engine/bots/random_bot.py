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

Only Purchase is randomized (per spec); Combat Move and Non-Combat Move
are both deterministic given the board state, which keeps a driven game
reproducible for a given purchase RNG seed.
"""
import copy
import random

from ..engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from ..movement import (
    _is_ally_or_self, graph_distances, legal_air_move_destinations,
    legal_combat_move_paths, legal_noncombat_move_paths, trace_combat_move,
)
from ..state import FactionMode


class RandomBot:
    def __init__(self, engine, faction, rng=None):
        self.engine = engine
        self.faction = faction
        self.rng = rng or random.Random()

    # ---- Purchase -----------------------------------------------------

    def take_purchase_phase(self):
        gs = self.engine.game_state
        treasury = gs.factions[self.faction].treasury_mpc
        unit_defs = self.engine.data.units()
        purchasable_types = [t for t, d in unit_defs.items() if d.get('purchasable')]

        sc_targets, other_targets = self._purchase_target_pools()
        orders = []
        if purchasable_types and sc_targets:
            sc_budget = self.rng.uniform(0, 0.55) * treasury
            orders = self._random_fill_purchases(orders, sc_targets, purchasable_types, sc_budget)
        if purchasable_types and other_targets:
            orders = self._random_fill_purchases(orders, other_targets, purchasable_types, treasury)

        self.engine.submit_purchases(self.faction, orders)
        self.engine.confirm_purchases(self.faction)

    def _purchase_target_pools(self):
        """(sc_targets, other_targets): every territory `faction` could
        legally purchase at (per GameEngine._purchase_sources -- a land
        territory it owns, or a sea zone adjacent to at least one of its
        owned land territories), split by whether a Strategic Center's
        capacity/cost is actually in play there. A sea target counts as
        an SC target if ANY of its eligible sources
        (_purchase_sources -- SC-first) is a Strategic Center."""
        terrs = self.engine.data.territories()
        adjacency = self.engine.data.adjacency()
        gs = self.engine.game_state

        owned_land = [tid for tid, t in gs.territories.items() if terrs[tid]['type'] == 'land' and t.owner == self.faction]
        sc_targets = {tid for tid in owned_land if terrs[tid].get('strategic_center')}
        other_targets = set(owned_land) - sc_targets

        sea_candidates = set()
        for tid in owned_land:
            for n in adjacency.get(tid, []):
                if terrs[n]['type'] == 'sea':
                    sea_candidates.add(n)
        for sea_tid in sea_candidates:
            sources = self.engine._purchase_sources(sea_tid, self.faction)
            if any(terrs[s].get('strategic_center') for s in sources):
                sc_targets.add(sea_tid)
            else:
                other_targets.add(sea_tid)

        return list(sc_targets), list(other_targets)

    def _random_fill_purchases(self, orders, targets, unit_types, budget_cap, max_consecutive_failures=25):
        failures = 0
        while failures < max_consecutive_failures:
            unit_type = self.rng.choice(unit_types)
            target = self.rng.choice(targets)
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

    # ---- Combat Move ----------------------------------------------------

    def take_combat_move_phase(self):
        """Plans against a single working copy of the game state, applying
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

        self._submit_incrementally(orders, self.engine.submit_combat_moves, self.engine.confirm_combat_moves)

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
        """Same single-working-copy planning approach as
        take_combat_move_phase, for the same reason (avoid an O(units)
        number of whole-board deep copies)."""
        self.engine.process_return_to_base(self.faction)  # mutates the real game state; must run before the copy below
        working = copy.deepcopy(self.engine.game_state)
        unit_defs = self.engine.data.units()
        orders = []

        for tid in list(working.territories.keys()):
            for u in list(working.territories[tid].units):
                if u.owner != self.faction or u.has_moved_noncombat:
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

        self._submit_incrementally(orders, self.engine.submit_noncombat_moves, self.engine.confirm_noncombat_moves)

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

    # ---- shared submission helper ---------------------------------------

    def _submit_incrementally(self, orders, submit_fn, confirm_fn):
        """Tries the whole candidate list in one call first -- the fast,
        common path, since every order here was already checked for
        legality in isolation when picked, so the whole batch is
        usually legal together too. submit_combat_moves/
        submit_noncombat_moves each deep-copy the entire GameState to
        validate, so this matters: falling back to accepting one order
        at a time (dropping whichever turn out illegal once earlier
        ones in the same batch are applied -- e.g. two units both
        wanting to move through the same just-captured territory) is
        only worth its O(n) extra deep-copies in the rare case the
        whole batch doesn't already work. Always confirms, even an
        empty list, so the phase's own `_*_confirmed` guard is satisfied
        for a unitless faction/turn."""
        try:
            submit_fn(self.faction, orders)
            confirm_fn(self.faction)
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
        confirm_fn(self.faction)
