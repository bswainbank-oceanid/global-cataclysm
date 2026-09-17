"""
GameEngine: the phased order-submission API wrapping a GameState,
exposed identically to human and bot callers (see docs/GAME_ARCHITECTURE.md's
build plan, Step 4). Every phase is now implemented -- Purchase covers
data/rules.json's full `purchase` section (location targeting, the
start-of-turn ownership snapshot, SC-discounted cost, the SC-first-then-
most-remaining-capacity multi-territory allocation with spillover,
naval/land location restrictions, and the contested-land Infantry-only
restriction); Deploy + Income covers placing pending_deployment onto the
board (with the carrierless-air and lost-contested-purchase fallbacks,
and hostile-sea-zone deploys becoming contested), income collection, and
the global recovery/heal sweep; Combat Move covers relocating units
along a validated path and marking every foreign territory entered or
passed through contested (never an immediate capture, even an
undefended Mechanized Infantry blitz -- ownership is resolved later, in
Capture Territory); Combat Resolution gathers each declared battle's
units per combat.multi_party_battles (the active faction's own units as
attacker, every non-allied faction present pooled as defender), drives
combat.resolve_battle() to completion, cleans up the dead, and runs the
sea-battle emergency-landing check; Non-Combat Move relocates units that
didn't combat-move this turn (air exempt from that exclusivity) -- no
contested-marking of its own, since entering an already-contested
territory is simply a legal destination and any newly-arrived unit is
picked up automatically by the next Combat Resolution or Capture
Territory pass based purely on presence; Capture Territory claims every
territory faction is contesting where no non-allied LAND units remain
(an undefended entry nobody ever fought over, or a battle faction won
outright even if air-only survivors linger on the other side), leaving
ownership untouched wherever a genuine contest between other factions is
still live. process_elimination_check (victory.elimination_rule, meant
to run right after Capture Territory) sweeps every faction's Strategic
Center count and eliminates -- clears remaining units, and excludes from
GameState.active_factions() forever after, which is what actually enforces
"no more turns" -- anyone down to 1 or 0. process_game_end_check
(victory.game_end_rule, meant to run once at the very end of a
faction's full turn, after the stubbed Alliances phase) sets
GameState.game_over once every remaining active faction is mutually
allied with every other -- nobody non-allied left to keep fighting --
but first gives the faction whose turn is ending one last chance to
withdraw from its alliance instead, which keeps the game going; that's
the only alliance-withdrawal action implemented anywhere in the engine,
the rest of alliances remaining a v1 stub.

Orchestration: advance_phase() steps GameState.phase through the fixed
7-phase sequence; advance_turn() (call once, right after
process_game_end_check, at the end of the Alliances phase) closes out
the active faction's turn -- resetting ITS units' has_moved_combat/
has_moved_noncombat flags and clearing it from every phase-confirmation
guard set, both of which nothing else in the engine ever does, so
skipping this is what used to make a second turn for any faction
impossible -- then advances active_faction to the next one in
active_factions() (wrapping around, dynamically skipping anyone eliminated
since), bumps global_turn, and resets phase to PURCHASE. A driver loop
is expected to call the phase methods in turn_order's order for
active_faction each turn, calling advance_phase() between them and
advance_turn() at the end, and to stop once GameState.game_over is set.

Rollback (phase_confirmation): submit_purchases/submit_combat_moves/
submit_noncombat_moves all take the COMPLETE desired order list every
call, wholesale-replacing any previously staged list for that faction --
there's no separate undo_last()/append() API. A caller "undoes" a choice
simply by calling submit_* again with a corrected list; nothing is
written to GameState until the matching confirm_*() is called, which is
irreversible for that faction's turn (submit_* and confirm_* both then
refuse further calls for that faction, for that phase). Deploy + Income
and Combat Resolution have no such staging -- both are automatic/
irreversible by nature (see turn_order's entries for each; Combat
Resolution specifically because dice have already been rolled), so
they're each just one direct call.
"""
import copy
import random
from dataclasses import dataclass

from . import data as _default_data
from .combat import BattleResult, EventKind, resolve_battle
from .economy import compute_income
from .movement import (
    _is_ally_or_self, find_emergency_landing, legal_air_move_destinations,
    legal_noncombat_move_destinations, trace_combat_move,
)
from .state import Phase, FactionMode, UnitInstance

# turn_order's fixed 7-phase sequence for one faction's full turn.
_PHASE_ORDER = [
    Phase.PURCHASE, Phase.COMBAT_MOVE, Phase.COMBAT_RESOLUTION, Phase.NONCOMBAT_MOVE,
    Phase.CAPTURE, Phase.DEPLOY_INCOME, Phase.ALLIANCES,
]


@dataclass
class PurchaseOrder:
    unit_type: str
    qty: int
    deploy_at: int  # territory_id, land or sea -- the actual final deploy target


@dataclass
class CombatMoveOrder:
    unit_id: int
    # The FULL route: path[0] must be wherever the unit currently is,
    # path[-1] the chosen destination, every consecutive pair adjacent.
    # Required even for a single-hop move (then just [origin, dest]) --
    # not just a destination -- because a Mechanized Infantry blitz's
    # intermediate captures depend on the actual route taken, not only
    # the endpoint (see movement.trace_combat_move). Air units, which
    # have no hop-by-hop legality or capture concerns, must still supply
    # exactly a 2-entry [origin, destination] path.
    path: list


@dataclass
class NonCombatMoveOrder:
    unit_id: int
    # Just the final destination -- unlike CombatMoveOrder, a non-combat
    # move never captures anything along the way (there's no blitz
    # concept off the combat-move phase), so there's nothing a full path
    # would add: legal_noncombat_move_destinations/legal_air_move_destinations
    # already do their own BFS internally and only the endpoint matters.
    destination: int


class GameEngine:
    def __init__(self, game_state, data_module=None, stats=None):
        self.game_state = game_state
        self.data = data_module or _default_data
        # Optional stats.GameStats observer -- if given, deploys,
        # captures, promotions, deaths, and kills are reported into it as
        # they happen (see stats.py's module docstring). None (the
        # default) means no observation at all; every other behavior
        # here is identical either way.
        self.stats = stats
        self._staged_purchases = {}  # faction_code -> [PurchaseOrder, ...]
        self._purchases_confirmed = set()
        self._staged_combat_moves = {}  # faction_code -> [CombatMoveOrder, ...]
        self._combat_moves_confirmed = set()
        self._combat_resolved = set()  # faction_codes that have already run resolve_combat this turn
        self._staged_noncombat_moves = {}  # faction_code -> [NonCombatMoveOrder, ...]
        self._noncombat_moves_confirmed = set()
        self._return_to_base_processed = set()  # faction_codes that have already run process_return_to_base this turn

    def _purchase_sources(self, deploy_at, faction):
        """Ordered list of territory_ids whose capacity/cost apply to a
        purchase targeting `deploy_at`: just [deploy_at] if it's a land
        territory `faction` owns (empty list if not -- an illegal
        target); for a sea zone, every adjacent land territory `faction`
        owns, Strategic Center(s) first, then by deploy cap descending
        (ties broken by territory_id for determinism) -- see
        rules.json's purchase.multi_adjacent_allocation_order."""
        terrs = self.data.territories()
        if terrs[deploy_at]['type'] == 'land':
            return [deploy_at] if self.game_state.territories[deploy_at].owner == faction else []
        neighbors = self.data.adjacency().get(deploy_at, [])
        owned_land = [
            tid for tid in neighbors
            if terrs[tid]['type'] == 'land' and self.game_state.territories[tid].owner == faction
        ]

        def sort_key(tid):
            terr = terrs[tid]
            is_sc = bool(terr.get('strategic_center'))
            cap = terr['value'] + (2 if is_sc else 0)
            return (0 if is_sc else 1, -cap, tid)
        return sorted(owned_land, key=sort_key)

    def _deploy_cap(self, territory_id):
        terr = self.data.territories()[territory_id]
        return terr['value'] + (2 if terr.get('strategic_center') else 0)

    def _unit_cost(self, unit_type, territory_id):
        terr = self.data.territories()[territory_id]
        unit_def = self.data.units()[unit_type]
        return unit_def['sc_cost'] if terr.get('strategic_center') else unit_def['cost']

    def _resolve_and_cost(self, orders, faction):
        """Shared validation/costing pass -- one run through `orders`,
        tracking capacity consumed per source territory across the
        WHOLE list (so two orders competing for the same sea zone's
        adjacent territories are charged correctly in submission order).
        Returns (total_cost, allocations), where `allocations` is a list
        parallel to `orders`, each entry a list of (source_territory_id,
        qty) pairs recording exactly which territory's capacity/cost
        paid for how many of that order's units -- more than one pair
        when a single order spills over across multiple adjacent
        territories (purchase.multi_adjacent_allocation_order). Raises
        ValueError on the first illegal order; nothing from `orders` is
        applied to GameState by this method regardless -- it's a pure
        check, used identically by submit_purchases (validate only,
        discards allocations) and confirm_purchases (re-run just before
        placing units, since it's cheap and keeps placement -- which
        needs the allocation detail to tag each UnitInstance.
        purchased_at correctly -- from silently drifting out of sync
        with validation)."""
        unit_defs = self.data.units()
        terrs = self.data.territories()
        consumed = {}
        total_cost = 0
        allocations = []

        for order in orders:
            if order.qty <= 0:
                raise ValueError(f'{order.unit_type}: qty must be positive')
            unit_def = unit_defs.get(order.unit_type)
            if unit_def is None or not unit_def.get('purchasable'):
                raise ValueError(f'{order.unit_type} is not a purchasable unit type')
            deploy_at = order.deploy_at
            if deploy_at not in terrs:
                raise ValueError(f'unknown territory {deploy_at}')
            deploy_terr = terrs[deploy_at]
            deploy_state = self.game_state.territories[deploy_at]

            if deploy_terr['type'] == 'land':
                if deploy_state.owner != faction:
                    raise ValueError(f'{faction} does not own land territory {deploy_at}')
                if unit_def['category'] == 'Sea':
                    raise ValueError(f'{order.unit_type} cannot deploy on land (territory {deploy_at})')
                if deploy_state.contested_by and order.unit_type != 'Infantry':
                    raise ValueError(f'only Infantry may be deployed into contested territory {deploy_at}')

            sources = self._purchase_sources(deploy_at, faction)
            if not sources:
                raise ValueError(f'{faction} has no owned territory to charge a purchase at {deploy_at} against')

            remaining = order.qty
            order_alloc = []
            for source in sources:
                if remaining <= 0:
                    break
                available = self._deploy_cap(source) - consumed.get(source, 0)
                if available <= 0:
                    continue
                take = min(available, remaining)
                consumed[source] = consumed.get(source, 0) + take
                total_cost += take * self._unit_cost(order.unit_type, source)
                remaining -= take
                order_alloc.append((source, take))
            if remaining > 0:
                raise ValueError(
                    f'not enough deploy capacity for {order.qty}x {order.unit_type} at {deploy_at} '
                    f'(short by {remaining} across every eligible territory)'
                )
            allocations.append(order_alloc)

        return total_cost, allocations

    def submit_purchases(self, faction, orders):
        """Validates and stages `orders` (a list of PurchaseOrder) as
        the COMPLETE desired purchase list for `faction` this turn --
        replaces any previously staged list outright, so resubmitting a
        shorter/corrected list is how a caller "undoes" a prior choice.
        Raises ValueError (nothing staged) if any order is illegal, or
        if the total cost exceeds treasury_mpc. Returns the total MPC
        cost on success."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction and cannot submit purchases')
        if self.game_state.phase != Phase.PURCHASE:
            raise ValueError('submit_purchases is only valid during the Purchase phase')
        if faction in self._purchases_confirmed:
            raise ValueError(f'{faction} has already confirmed purchases this turn')

        total_cost, _ = self._resolve_and_cost(orders, faction)
        if total_cost > self.game_state.factions[faction].treasury_mpc:
            raise ValueError(
                f'{faction} purchase totals {total_cost} MPC, only '
                f'{self.game_state.factions[faction].treasury_mpc} available'
            )

        self._staged_purchases[faction] = list(orders)
        return total_cost

    def confirm_purchases(self, faction):
        """Commits `faction`'s currently-staged purchase list (empty if
        submit_purchases was never called) -- deducts total cost from
        treasury_mpc and places each order's units into its deploy_at
        territory's pending_deployment (not yet on the board; actually
        appearing there, plus the carrierless-air and lost-contested-
        purchase fallbacks, is deploy_and_collect_income's job).
        Irreversible: submit_purchases and confirm_purchases both refuse
        further calls for this faction this turn afterward."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if faction in self._purchases_confirmed:
            raise ValueError(f'{faction} has already confirmed purchases this turn')

        orders = self._staged_purchases.get(faction, [])
        total_cost, allocations = self._resolve_and_cost(orders, faction)
        unit_defs = self.data.units()

        for order, order_alloc in zip(orders, allocations):
            for source_tid, qty in order_alloc:
                for _ in range(qty):
                    instance = UnitInstance(
                        unit_id=self.game_state.new_unit_id(),
                        unit_type=order.unit_type,
                        owner=faction,
                        current_hp=unit_defs[order.unit_type]['hp'],
                        purchased_at=source_tid,
                    )
                    self.game_state.territories[order.deploy_at].pending_deployment.append(instance)

        self.game_state.factions[faction].treasury_mpc -= total_cost
        self._purchases_confirmed.add(faction)
        self._staged_purchases.pop(faction, None)

    def deploy_and_collect_income(self, faction):
        """The Deploy + Income phase for `faction`'s own turn: places
        its pending_deployment units onto the board (applying the
        carrierless-air and lost-contested-purchase fallbacks below),
        collects its income, then runs the global recovery/heal check
        over the WHOLE board -- every faction's units, not just this
        one (combat.recovery_rule). Automatic: no player choice, no
        rollback, and unlike Purchase this isn't gated on
        _purchases_confirmed -- a faction that never bought anything
        this turn still collects income and still triggers the global
        recovery sweep."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.DEPLOY_INCOME:
            raise ValueError('deploy_and_collect_income is only valid during the Deploy + Income phase')

        self._deploy_pending_units(faction)
        self.game_state.factions[faction].treasury_mpc += compute_income(faction, self.game_state, self.data)
        self._run_recovery_check()

    def _deploy_pending_units(self, faction):
        """Moves every pending_deployment unit owned by `faction`,
        across every territory, onto the actual board -- land targets
        go straight to TerritoryState.units unless the territory was
        lost this turn (purchase.contested_purchase_lost_during_turn_fallback),
        sea targets additionally check purchase.carrierless_air_deploy_fallback
        and purchase.hostile_sea_deploy_creates_contested."""
        terrs = self.data.territories()
        for tid in list(self.game_state.territories.keys()):
            t = self.game_state.territories[tid]
            pending = [u for u in t.pending_deployment if u.owner == faction]
            if not pending:
                continue
            t.pending_deployment = [u for u in t.pending_deployment if u.owner != faction]

            if terrs[tid]['type'] == 'sea':
                self._deploy_to_sea(tid, pending, faction)
            else:
                self._deploy_to_land(tid, pending, faction)

    def _deploy_to_land(self, tid, pending, faction):
        t = self.game_state.territories[tid]
        if t.owner == faction:
            t.units.extend(pending)
            self._record_deploys(faction, pending)
            return
        # Lost during the turn (only ever Infantry -- the only unit
        # type contested_land_deploy_restriction lets into a contested
        # territory in the first place) -- purchase.
        # contested_purchase_lost_during_turn_fallback's chain.
        fallback = self._find_fallback_for_lost_purchase(tid, faction)
        if fallback is not None:
            self.game_state.territories[fallback].units.extend(pending)
            self._record_deploys(faction, pending)
        # else: no adjacent controlled territory or sea zone -- lost outright, never placed.

    def _record_deploys(self, faction, units):
        if self.stats is None:
            return
        for u in units:
            self.stats.record_deploy(faction, u.unit_type)

    def _find_fallback_for_lost_purchase(self, tid, faction):
        """(1) an adjacent territory `faction` still controls; (2) if
        none, an adjacent sea zone; (3) if neither, None (the units are
        lost). No tie-break order is specified for multiple qualifying
        options, so this picks deterministically -- the lowest
        territory_id -- among whichever tier applies."""
        terrs = self.data.territories()
        neighbors = self.data.adjacency().get(tid, [])
        controlled_land = sorted(
            n for n in neighbors if terrs[n]['type'] == 'land' and self.game_state.territories[n].owner == faction
        )
        if controlled_land:
            return controlled_land[0]
        sea = sorted(n for n in neighbors if terrs[n]['type'] == 'sea')
        return sea[0] if sea else None

    def _deploy_to_sea(self, tid, pending, faction):
        unit_defs = self.data.units()
        t = self.game_state.territories[tid]

        # Carrierless air fallback: an Air-category unit whose sea zone
        # has no OWN Aircraft Carrier -- neither already present nor
        # arriving in this same batch -- redirects to the land territory
        # that actually funded it (UnitInstance.purchased_at), same
        # own-carrier-only standard as everywhere else carriers matter
        # (an ally's carrier doesn't count).
        has_own_carrier = any(u.unit_type == 'Aircraft Carrier' and u.owner == faction for u in t.units) or \
            any(u.unit_type == 'Aircraft Carrier' for u in pending)

        to_place_here = []
        for u in pending:
            if unit_defs[u.unit_type]['category'] == 'Air' and not has_own_carrier:
                self.game_state.territories[u.purchased_at].units.append(u)
            else:
                to_place_here.append(u)
        t.units.extend(to_place_here)
        self._record_deploys(faction, pending)  # both the redirected-to-land and placed-here units actually landed on the board

        # Hostile sea deploy creates contested, immediately, as a direct
        # result of the deployment (purchase.hostile_sea_deploy_creates_contested)
        # -- only if something of ours actually landed here (a fully
        # redirected air-only order leaves the zone untouched).
        if not to_place_here:
            return
        other_owners = {u.owner for u in t.units if u.owner != faction}
        non_allies = {o for o in other_owners if not _is_ally_or_self(self.game_state, faction, o)}
        if non_allies:
            t.contested_by = (t.contested_by or set()) | {faction} | non_allies

    def _run_recovery_check(self):
        """combat.recovery_rule, run at every faction's Deploy + Income:
        any unit on the WHOLE board heals to full HP once a full round
        has elapsed since it last took part in combat --
        current_global_turn - last_combat_global_turn >= num_factions,
        where num_factions is len(active_factions()) (turn_order_note)."""
        unit_defs = self.data.units()
        num_factions = len(self.game_state.active_factions())
        if num_factions == 0:
            return
        for t in self.game_state.territories.values():
            for u in t.units:
                if u.last_combat_global_turn is None:
                    continue
                if self.game_state.global_turn - u.last_combat_global_turn >= num_factions:
                    u.current_hp = u.effective_stats(unit_defs)['max_hp']

    def _find_unit(self, game_state, unit_id, faction):
        for t in game_state.territories.values():
            for u in t.units:
                if u.unit_id == unit_id:
                    if u.owner != faction:
                        raise ValueError(f'unit {unit_id} does not belong to {faction}')
                    return u, t.territory_id
        raise ValueError(f'unit {unit_id} not found on the board (pending_deployment units cannot move this turn)')

    def _mark_contested_by_attack(self, dest_state, faction, game_state):
        """Adds `faction` and every non-allied defender (both current
        occupants and, for a land territory, its registered owner) to
        contested_by -- combat_move_destination's "relocating into the
        target is what makes it contested"."""
        defenders = {u.owner for u in dest_state.units if not _is_ally_or_self(game_state, faction, u.owner)}
        if dest_state.owner and not _is_ally_or_self(game_state, faction, dest_state.owner):
            defenders.add(dest_state.owner)
        dest_state.contested_by = (dest_state.contested_by or set()) | {faction} | defenders

    def _execute_combat_moves(self, orders, faction, game_state):
        """Runs `orders` against `game_state`, relocating each unit
        along its validated path and applying every consequence as it
        goes -- so a LATER order in the same list can legitimately
        depend on an EARLIER one's capture (e.g. staging a second wave
        through ground the first wave just took). Raises ValueError on
        the first illegal order. Used identically by submit_combat_moves
        (against a throwaway deep copy, purely to validate) and
        confirm_combat_moves (against the real GameState).

        carrier_air_operations.carrier_ride_along, the combat-move case:
        an Aircraft Carrier's combat move sweeps along every one of
        faction's air units co-located in its origin -- EXCEPT any that
        have their own explicit order somewhere in this same `orders`
        list (precomputed once below), regardless of whether that
        order comes before or after the carrier's in submission order.
        Unlike the non-combat case, there's no valid "chaining" reading
        here (a plane can't meaningfully attack on its own AND then
        also ride into a second attack the same phase), so an
        unconditional exclusion is the right model, not physical-
        presence-at-the-moment-of-the-move alone. A swept rider is
        simply relocated to the carrier's destination and marked
        has_moved_combat -- nothing else is needed to make it an actual
        combatant, since Combat Resolution's gather_battle_units already
        includes anyone physically present, however they got there."""
        unit_defs = self.data.units()
        units_with_own_order = {o.unit_id for o in orders}
        for order in orders:
            if len(order.path) < 2:
                raise ValueError(f'unit {order.unit_id}: a combat move path needs at least an origin and a destination')
            unit, origin_id = self._find_unit(game_state, order.unit_id, faction)
            if unit.has_moved_combat:
                raise ValueError(f'unit {order.unit_id} has already made a combat move this turn')
            if origin_id != order.path[0]:
                raise ValueError(f'unit {order.unit_id} is at {origin_id}, not {order.path[0]}')

            category = unit_defs[unit.unit_type]['category']
            dest_id = order.path[-1]
            origin_state = game_state.territories[origin_id]
            dest_state = game_state.territories[dest_id]

            if category == 'Air':
                if len(order.path) != 2:
                    raise ValueError(f'unit {order.unit_id}: an air combat move path must be exactly [origin, destination]')
                legal = legal_air_move_destinations(unit.unit_type, faction, origin_id, 'combat', game_state, self.data)
                if dest_id not in legal:
                    raise ValueError(f'{dest_id} is not a legal air combat-move destination for unit {order.unit_id}')
                # Groundwork for process_return_to_base: remember where
                # this plane took off from, and which own carrier (if
                # any) was sitting there, BEFORE it leaves -- a survivor
                # snaps back to this at the start of Non-Combat Move.
                unit.combat_move_origin = origin_id
                unit.based_on_carrier = next(
                    (u.unit_id for u in origin_state.units if u.unit_type == 'Aircraft Carrier' and u.owner == faction),
                    None,
                )
                origin_state.units.remove(unit)
                dest_state.units.append(unit)
                self._mark_contested_by_attack(dest_state, faction, game_state)  # air alone can't capture, only attack
            else:
                trace = trace_combat_move(unit.unit_type, faction, order.path, game_state, self.data)
                riders = []
                if unit.unit_type == 'Aircraft Carrier':
                    riders = [
                        u for u in origin_state.units
                        if u.owner == faction and u.unit_id not in units_with_own_order
                        and unit_defs[u.unit_type]['category'] == 'Air'
                    ]
                origin_state.units.remove(unit)
                # Every foreign territory entered or passed through this
                # way is marked contested -- never captured outright,
                # even an entirely undefended Mechanized Infantry blitz
                # (confirmed this session). Actual ownership is resolved
                # later, in the Capture Territory phase, from whatever
                # the board looks like by then.
                for entered_tid in trace.entered_en_route:
                    self._mark_contested_by_attack(game_state.territories[entered_tid], faction, game_state)
                if trace.final_kind in ('capture', 'attack', 'join_contest'):
                    self._mark_contested_by_attack(dest_state, faction, game_state)
                # 'safe_landing': already friendly -- no contested change
                dest_state.units.append(unit)

                for rider in riders:
                    # Same return-to-base bookkeeping an air unit's own
                    # combat move gets -- a swept rider is just as
                    # eligible to snap back to this carrier afterward.
                    rider.combat_move_origin = origin_id
                    rider.based_on_carrier = unit.unit_id
                    origin_state.units.remove(rider)
                    dest_state.units.append(rider)
                    rider.has_moved_combat = True

            unit.has_moved_combat = True

    def submit_combat_moves(self, faction, orders):
        """Validates and stages `orders` (a list of CombatMoveOrder) as
        the COMPLETE desired combat-move list for `faction` this turn --
        replaces any previously staged list outright, so resubmitting a
        corrected list is how a caller "undoes" a prior choice. Runs the
        full move/capture/contest sequence against a throwaway deep copy
        of GameState purely to validate it (discarded either way) --
        nothing real is touched here. Raises ValueError (nothing staged)
        if any order is illegal."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction and cannot submit combat moves')
        if self.game_state.phase != Phase.COMBAT_MOVE:
            raise ValueError('submit_combat_moves is only valid during the Combat Move phase')
        if faction in self._combat_moves_confirmed:
            raise ValueError(f'{faction} has already confirmed combat moves this turn')

        working = copy.deepcopy(self.game_state)
        self._execute_combat_moves(orders, faction, working)

        self._staged_combat_moves[faction] = list(orders)

    def confirm_combat_moves(self, faction):
        """Commits `faction`'s currently-staged combat-move list (empty
        if submit_combat_moves was never called) -- re-runs the exact
        same validated sequence against the real GameState. Irreversible:
        submit_combat_moves and confirm_combat_moves both refuse further
        calls for this faction this turn afterward."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if faction in self._combat_moves_confirmed:
            raise ValueError(f'{faction} has already confirmed combat moves this turn')

        orders = self._staged_combat_moves.get(faction, [])
        self._execute_combat_moves(orders, faction, self.game_state)
        self._combat_moves_confirmed.add(faction)
        self._staged_combat_moves.pop(faction, None)

    def declared_battles(self, faction):
        """Every battle `faction`'s own Combat Resolution phase must
        fight this turn: every territory that's currently marked
        contested AND where `faction` has at least one unit present --
        covers both a fresh attack declared this turn and a standing
        multi-turn stalemate `faction` is still part of (combat.
        multi_party_battles.attacker_always_solo: a faction's units only
        ever fight during THAT faction's own Combat Resolution, never
        pooled with another faction's). Returns [(territory_id,
        battle_type), ...] in combat.battle_resolution_pass_order (every
        sea battle before any land battle)."""
        terrs = self.data.territories()
        battles = []
        for tid, t in self.game_state.territories.items():
            if not t.contested_by:
                continue
            if not any(u.owner == faction for u in t.units):
                continue
            battles.append((tid, 'sea' if terrs[tid]['type'] == 'sea' else 'land'))
        pass_order = {'sea': 0, 'land': 1}
        battles.sort(key=lambda b: pass_order[b[1]])
        return battles

    def gather_battle_units(self, territory_id, faction):
        """(attacker_units, defender_units) for a battle at
        `territory_id` from `faction`'s perspective, per combat.
        multi_party_battles: attacker_units is exactly faction's own
        units there; defender_units is every OTHER unit there whose
        owner isn't an ally of faction, pooled into one list regardless
        of how many distinct factions that covers. A pure query -- takes
        no action, so a future interactive UI can call this directly to
        set up its own combat.resolve_battle() generator instead of
        going through the auto-play resolve_combat() below."""
        t = self.game_state.territories[territory_id]
        attacker_units = [u for u in t.units if u.owner == faction]
        defender_units = [u for u in t.units if u.owner != faction and not _is_ally_or_self(self.game_state, faction, u.owner)]
        return attacker_units, defender_units

    def resolve_combat(self, faction, rng=None):
        """Auto-plays every one of `faction`'s declared_battles this
        turn, sea then land, applying every consequence as each one
        finishes -- dead units removed, contested_by cleared once a
        battle is decisively won or wiped out (left set if the 3-round
        cap left both sides still standing -- see combat.
        contested_territory_rule, refought next time declared_battles
        picks it up again), and the sea-battle emergency-landing check
        (combat.emergency_landing). Returns a list of BattleResult, one
        per battle, in the order resolved.

        Not reversible (phase_confirmation.scope: dice have already been
        rolled) -- there's no staging here, this is the real thing the
        moment it's called. Can only be called once per faction per turn
        (a second call would otherwise re-fight any still-contested
        standoff a second time within the same phase)."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.COMBAT_RESOLUTION:
            raise ValueError('resolve_combat is only valid during the Combat Resolution phase')
        if faction in self._combat_resolved:
            raise ValueError(f'{faction} has already resolved combat this turn')
        self._combat_resolved.add(faction)

        rng = rng or random.Random()
        unit_defs = self.data.units()
        rules = self.data.rules()
        results = []
        for territory_id, battle_type in self.declared_battles(faction):
            attacker_units, defender_units = self.gather_battle_units(territory_id, faction)
            events = list(resolve_battle(
                attacker_units, defender_units, battle_type, rng,
                self.game_state.global_turn, unit_defs, rules,
            ))
            result = BattleResult.from_events(events)
            self._record_combat_stats(events, attacker_units, defender_units, battle_type)
            self._apply_battle_outcome(territory_id, battle_type, faction, result, rng)
            results.append(result)
        return results

    def _record_combat_stats(self, events, attacker_units, defender_units, battle_type):
        """Promotions and kills come straight off the event stream
        (PROMOTION events, and any UNIT_ROLL hit that drops its target's
        target_hp_after to <=0 -- the same "killing blow" signal
        combat._fight_one_round uses internally for its own killed_by XP
        bonus, just re-derived here from the public events rather than
        threaded through as a return value). Deaths come off the final
        BATTLE_END event's eliminated_*_ids -- battle_type == 'sea' plus
        the dead unit's own category == 'Land' is what "died in transport
        form" means (see stats.py's module docstring)."""
        if self.stats is None:
            return
        unit_defs = self.data.units()
        by_id = {u.unit_id: u for u in attacker_units + defender_units}
        for event in events:
            if event.kind == EventKind.UNIT_ROLL and event.hit and event.target_hp_after is not None and event.target_hp_after <= 0:
                killer = by_id.get(event.unit_id)
                if killer is not None:
                    self.stats.record_kill(killer.owner, killer.unit_type)
            elif event.kind == EventKind.PROMOTION:
                unit = by_id.get(event.promoted_unit_id)
                if unit is not None:
                    self.stats.record_promotion(unit.owner, unit.unit_type)
            elif event.kind == EventKind.BATTLE_END:
                for uid in (event.eliminated_attacker_ids or []) + (event.eliminated_defender_ids or []):
                    unit = by_id.get(uid)
                    if unit is None:
                        continue
                    in_transport = battle_type == 'sea' and unit_defs[unit.unit_type]['category'] == 'Land'
                    self.stats.record_death(unit.owner, unit.unit_type, in_transport=in_transport)

    def _apply_battle_outcome(self, territory_id, battle_type, faction, result, rng):
        t = self.game_state.territories[territory_id]
        dead_ids = set(result.eliminated_attacker_ids) | set(result.eliminated_defender_ids)
        t.units = [u for u in t.units if u.unit_id not in dead_ids]

        if battle_type == 'sea':
            self._resolve_stranded_defender_aircraft(territory_id, result, rng)

        if not self._attacking_coalition_has_ground_or_naval_presence(t, faction):
            # combat.contested_territory_rule's claiming update: if
            # `faction` and its current allies have no LAND or SEA unit
            # left here at all -- air alone can't hold a claim, the same
            # standard Capture Territory already applies ("air can't
            # capture") -- the contest is over immediately, whatever the
            # battle's own outcome was: a decisive loss, a mutual wipe,
            # or even a 3-round stalemate where the attacker's own
            # ground/naval force didn't survive it while the defender's
            # did. No longer contested also means no longer a legal
            # non-combat-move destination for anyone else (a clean,
            # non-allied foreign territory never is).
            t.contested_by = None
            return

        if battle_type == 'sea' and result.outcome != 'contested':
            # Sea has no Capture Territory equivalent to later resolve a
            # decisive win into an ownership change -- once the fight
            # itself is over (not a 3-round stalemate still to refight),
            # there's nothing left here to track.
            t.contested_by = None
            return

        # Land, attacker's coalition still has ground/naval presence:
        # leave contested_by exactly as it is, whatever result.outcome
        # was (including a decisive win) -- Capture Territory, not here,
        # is what actually resolves a won attack into an ownership
        # change, and it makes that call from the board state this
        # leaves behind (its own non-allied-land-units check), not from
        # this battle's outcome label.

    def _attacking_coalition_has_ground_or_naval_presence(self, territory_state, faction):
        """True if `faction` or any of its current allies still has at
        least one LAND or SEA unit (Air excluded -- air alone can't hold
        a claim, same standard as Capture Territory) physically present
        in `territory_state`, right after a battle there resolves."""
        unit_defs = self.data.units()
        return any(
            _is_ally_or_self(self.game_state, faction, u.owner) and unit_defs[u.unit_type]['category'] in ('Land', 'Sea')
            for u in territory_state.units
        )

    def _resolve_stranded_defender_aircraft(self, territory_id, result, rng):
        """combat.emergency_landing: once a sea battle concludes, any
        SURVIVING defending aircraft left without their own faction's
        carrier get a one-hop emergency landing search -- scoped
        per-owner, since defenders can be pooled across several
        factions (combat.multi_party_battles) and each only cares about
        ITS OWN carrier surviving. Attacking aircraft have no equivalent
        rescue (defender only) -- untouched here."""
        t = self.game_state.territories[territory_id]
        unit_defs = self.data.units()
        surviving_defender_ids = set(result.surviving_defender_ids)
        defenders_here = [u for u in t.units if u.unit_id in surviving_defender_ids]
        owners_with_surviving_carrier = {u.owner for u in defenders_here if u.unit_type == 'Aircraft Carrier'}
        for u in defenders_here:
            if unit_defs[u.unit_type]['category'] != 'Air' or u.owner in owners_with_surviving_carrier:
                continue
            landing = find_emergency_landing(territory_id, u.owner, self.game_state, self.data, rng)
            t.units.remove(u)
            if landing is not None:
                self.game_state.territories[landing].units.append(u)
            # else: no adjacent own carrier, own land, or allied land -- lost

    def process_return_to_base(self, faction):
        """carrier_air_operations.return_to_base_after_combat: an
        automated step at the very start of the Non-Combat Move phase --
        call this BEFORE submit_noncombat_moves, which refuses to run
        until it has. Every surviving air unit that made a combat move
        this turn (UnitInstance.combat_move_origin set, stamped by
        _execute_combat_moves) snaps back to wherever it took off from --
        that same origin territory, or, if it took off from a carrier's
        sea zone, that SAME carrier specifically, wherever it currently
        is, even if the carrier has since moved on its own. This
        REPLACES the unit's non-combat move for the turn (has_moved_noncombat
        is set True by it, same as a real move).

        If the recorded carrier didn't survive, or the recorded
        territory is no longer a legal landing spot for this unit (own-
        or-allied, matching legal_air_move_destinations' own standard --
        practically this only ever bites the carrier case, since a
        LAND territory the active faction owned at combat-move time
        can't change hands mid-turn, nobody else acts on this faction's
        own turn), nothing is auto-applied -- the unit is simply left
        exactly where it is, free to receive a normal non-combat move
        order instead (the documented 'gets a regular non-combat move to
        land' fallback).

        Automatic and irreversible, like Deploy + Income and Combat
        Resolution -- no staging, no undo, and only callable once per
        faction per turn."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.NONCOMBAT_MOVE:
            raise ValueError('process_return_to_base is only valid during the Non-Combat Move phase')
        if faction in self._return_to_base_processed:
            raise ValueError(f'{faction} has already processed return-to-base this turn')
        self._return_to_base_processed.add(faction)

        pending_unit_ids = [
            u.unit_id for t in self.game_state.territories.values() for u in t.units
            if u.owner == faction and u.combat_move_origin is not None
        ]
        for unit_id in pending_unit_ids:
            unit, current_id = self._find_unit(self.game_state, unit_id, faction)
            target = self._resolve_return_to_base_target(unit, faction)
            unit.combat_move_origin = None
            unit.based_on_carrier = None
            if target is None:
                continue
            if target != current_id:
                self.game_state.territories[current_id].units.remove(unit)
                self.game_state.territories[target].units.append(unit)
            unit.has_moved_noncombat = True

    def _resolve_return_to_base_target(self, unit, faction):
        """Where `unit` should snap back to, or None if that's no
        longer possible (falls through to a regular non-combat move)."""
        if unit.based_on_carrier is not None:
            for t in self.game_state.territories.values():
                if any(u.unit_id == unit.based_on_carrier for u in t.units):
                    return t.territory_id
            return None  # that carrier didn't survive
        origin_id = unit.combat_move_origin
        origin_terr = self.data.territories()[origin_id]
        origin_state = self.game_state.territories[origin_id]
        if origin_terr['type'] == 'land':
            return origin_id if _is_ally_or_self(self.game_state, faction, origin_state.owner) else None
        # Sea origin with no carrier recorded (departed from open water,
        # unusual but not impossible) -- only safe if faction's own
        # carrier happens to be there now.
        if any(u.unit_type == 'Aircraft Carrier' and u.owner == faction for u in origin_state.units):
            return origin_id
        return None

    def _execute_noncombat_moves(self, orders, faction, game_state):
        """Runs `orders` (a list of NonCombatMoveOrder) against
        `game_state` in order, relocating each unit if its destination
        is legal. No capture, no contested-marking to apply -- entering
        a territory the mover is already contesting (or one contested by
        anyone else, for that matter) is simply a legal destination
        (movement.noncombat_move_destination); any newly-arrived unit is
        automatically picked up by the NEXT Combat Resolution pass
        through gather_battle_units, which only cares about who's
        physically present, not how they got there -- no extra
        contested_by bookkeeping needed here. Raises ValueError on the
        first illegal order. Used identically by submit_noncombat_moves
        (against a throwaway deep copy) and confirm_noncombat_moves
        (against the real GameState).

        carrier_air_operations.carrier_ride_along: moving an Aircraft
        Carrier here also sweeps along every one of `faction`'s air
        units CURRENTLY co-located in the carrier's origin -- covers the
        default ride-along (no order of its own -- still there when the
        carrier moves) and chaining (its OWN earlier order in this same
        list flew it onto the carrier -- still co-located when the
        carrier's order comes later) uniformly, since both are just
        "physically there when the carrier moves," with no need to
        distinguish them. A plane that pre-empts by moving AWAY via its
        own earlier order is naturally excluded -- it's simply not there
        anymore by the time the carrier's order runs."""
        unit_defs = self.data.units()
        for order in orders:
            unit, origin_id = self._find_unit(game_state, order.unit_id, faction)
            if unit.has_moved_noncombat:
                raise ValueError(f'unit {order.unit_id} has already made a non-combat move this turn')

            category = unit_defs[unit.unit_type]['category']
            if category == 'Air':
                # movement.combat_or_noncombat_not_both: air is exempt
                # from the combat-move/non-combat-move exclusivity --
                # may non-combat-move even after already combat-moving.
                legal = legal_air_move_destinations(unit.unit_type, faction, origin_id, 'noncombat', game_state, self.data)
            else:
                if unit.has_moved_combat:
                    raise ValueError(f'unit {order.unit_id} already made a combat move this turn and cannot also non-combat-move')
                legal = legal_noncombat_move_destinations(unit.unit_type, faction, origin_id, game_state, self.data)

            if order.destination not in legal:
                raise ValueError(f'{order.destination} is not a legal non-combat move destination for unit {order.unit_id}')

            riders = []
            if unit.unit_type == 'Aircraft Carrier':
                riders = [
                    u for u in game_state.territories[origin_id].units
                    if u.owner == faction and unit_defs[u.unit_type]['category'] == 'Air'
                ]

            game_state.territories[origin_id].units.remove(unit)
            game_state.territories[order.destination].units.append(unit)
            unit.has_moved_noncombat = True

            for rider in riders:
                game_state.territories[origin_id].units.remove(rider)
                game_state.territories[order.destination].units.append(rider)
                rider.has_moved_noncombat = True

    def submit_noncombat_moves(self, faction, orders):
        """Validates and stages `orders` (a list of NonCombatMoveOrder)
        as the COMPLETE desired non-combat-move list for `faction` this
        turn -- replaces any previously staged list outright, so
        resubmitting a corrected list is how a caller "undoes" a prior
        choice. Runs the full sequence against a throwaway deep copy of
        GameState purely to validate it (discarded either way) --
        nothing real is touched here. Raises ValueError (nothing staged)
        if any order is illegal."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction and cannot submit non-combat moves')
        if self.game_state.phase != Phase.NONCOMBAT_MOVE:
            raise ValueError('submit_noncombat_moves is only valid during the Non-Combat Move phase')
        if faction in self._noncombat_moves_confirmed:
            raise ValueError(f'{faction} has already confirmed non-combat moves this turn')
        if faction not in self._return_to_base_processed:
            raise ValueError(f'process_return_to_base must run for {faction} before submit_noncombat_moves')

        working = copy.deepcopy(self.game_state)
        self._execute_noncombat_moves(orders, faction, working)

        self._staged_noncombat_moves[faction] = list(orders)

    def confirm_noncombat_moves(self, faction):
        """Commits `faction`'s currently-staged non-combat-move list
        (empty if submit_noncombat_moves was never called) -- re-runs
        the exact same validated sequence against the real GameState,
        then applies movement.stranded_aircraft_rule: any of faction's
        air units left sitting over a sea zone with no own carrier
        present (an ally's doesn't count) are lost. Irreversible:
        submit_noncombat_moves and confirm_noncombat_moves both refuse
        further calls for this faction this turn afterward."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if faction in self._noncombat_moves_confirmed:
            raise ValueError(f'{faction} has already confirmed non-combat moves this turn')

        orders = self._staged_noncombat_moves.get(faction, [])
        self._execute_noncombat_moves(orders, faction, self.game_state)
        self._apply_stranded_aircraft_check(faction)
        self._noncombat_moves_confirmed.add(faction)
        self._staged_noncombat_moves.pop(faction, None)

    def _apply_stranded_aircraft_check(self, faction):
        """movement.stranded_aircraft_rule: run once, at the end of the
        Non-Combat Move phase -- any of `faction`'s air units sitting
        over a sea zone with no OWN Aircraft Carrier present (an ally's
        never counts) are lost."""
        terrs = self.data.territories()
        unit_defs = self.data.units()
        for tid, t in self.game_state.territories.items():
            if terrs[tid]['type'] != 'sea':
                continue
            if any(u.unit_type == 'Aircraft Carrier' and u.owner == faction for u in t.units):
                continue
            t.units = [u for u in t.units if not (u.owner == faction and unit_defs[u.unit_type]['category'] == 'Air')]

    def process_capture_territory(self, faction):
        """The automated Capture Territory phase -- no player choice, no
        staging (phase_confirmation.scope), a single direct call like
        Deploy + Income and Combat Resolution. Scans every LAND
        territory where `faction` is listed in contested_by (it entered,
        attacked, or merely passed through there at some point this
        turn -- see Combat Move's execution_notes: every foreign
        territory entered is marked contested, never captured outright,
        even an undefended Mechanized Infantry blitz) and, if no LAND
        units belonging to a non-ally are currently present there,
        awards it to whichever of `faction`/its allies actually holds
        the ground now (see _determine_capture_winner) -- covers an
        undefended blitz-through, a battle faction (or an ally fighting
        alongside it) won outright even if the loser's only survivors
        are air units (air can't hold ground), and the case where
        faction's OWN land units were all wiped out but an ally's
        weren't (confirmed this session: the ally claims it, not
        faction, even though it's faction's own Capture Territory phase
        doing the awarding).

        If land units belonging to some OTHER, non-allied faction (or
        factions) are still there, ownership is left exactly as it
        stands -- even if the territory's registered owner has since
        been eliminated from the game entirely, as long as the contest
        between two (or more) factions other than `faction`/its allies is
        still live, nothing here resolves it in anyone's favor.
        Whichever faction actually ends up the sole remaining land
        claimant picks it up on ITS OWN Capture Territory phase instead
        -- this only ever settles a contest `faction` itself is part of
        (faction in contested_by)."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.CAPTURE:
            raise ValueError('process_capture_territory is only valid during the Capture Territory phase')

        terrs = self.data.territories()
        unit_defs = self.data.units()
        for tid, t in self.game_state.territories.items():
            if terrs[tid]['type'] != 'land':
                continue
            if not t.contested_by or faction not in t.contested_by:
                continue
            land_units_present = [u for u in t.units if unit_defs[u.unit_type]['category'] == 'Land']
            non_allied_land_owners = {
                u.owner for u in land_units_present if not _is_ally_or_self(self.game_state, faction, u.owner)
            }
            if non_allied_land_owners:
                continue
            previous_owner = t.owner
            t.owner = self._determine_capture_winner(faction, land_units_present, unit_defs)
            t.contested_by = None
            if self.stats is not None and previous_owner != t.owner:
                self.stats.record_capture(self.game_state.global_turn, t.owner, tid, previous_owner)

    def _determine_capture_winner(self, faction, land_units_present, unit_defs):
        """Who actually gets `faction`'s claim, among `faction` and its
        allies -- the only ones who CAN be in `land_units_present`,
        since the caller already ruled out non-allied presence.
        `faction` itself wins if it has even a single land unit here,
        no matter how few ('if you have one land unit, you can keep
        it'). Otherwise, among faction's allies who DO have land units
        present, the one with the greatest TOTAL cost of its land units
        here wins; ties broken by turn order (GameState.factions' fixed
        iteration order -- earlier wins). If literally nobody -- not
        faction, not any ally -- has land units present (faction simply
        ran through an empty territory and kept moving), faction still
        gets the claim by default, since it's the one whose Capture
        Territory phase is processing it and no ally has any better
        claim either."""
        if any(u.owner == faction for u in land_units_present):
            return faction

        by_owner = {}
        for u in land_units_present:
            by_owner.setdefault(u.owner, []).append(u)
        if not by_owner:
            return faction

        turn_order = list(self.game_state.factions.keys())

        def sort_key(owner):
            total_cost = sum(unit_defs[u.unit_type]['cost'] or 0 for u in by_owner[owner])
            return (total_cost, -turn_order.index(owner))

        return max(by_owner, key=sort_key)

    def process_elimination_check(self):
        """victory.elimination_rule: any HUMAN/BOT/DEFENSIVE faction
        currently controlling <=1 Strategic Center (original or
        captured; a contested one still counts, since its
        TerritoryState.owner doesn't change until the contest actually
        resolves in Capture Territory -- see combat.contested_territory_rule)
        is eliminated -- FactionState.eliminated is set True, and every
        unit it still has anywhere on the board is removed immediately.
        NEUTRAL is skipped -- it never had turns to lose, and the
        concept doesn't meaningfully apply.

        Ownership only ever changes via process_capture_territory, so
        this should run right after it, once per turn -- automatic, no
        player choice, nothing to roll back, same as that phase. Global:
        sweeps every faction's SC count, not just whichever one's turn
        it is, since one faction's own Capture Territory can reduce
        ANOTHER faction down to elimination. Idempotent -- safe to call
        even when nothing changed; an already-eliminated faction is
        simply skipped.

        GameState.active_factions() is what actually enforces "gets no
        more turns" -- it excludes eliminated factions, and every phase
        method in this class gates on it, so nothing else needed to
        change to make that stick."""
        if self.game_state.phase != Phase.CAPTURE:
            raise ValueError('process_elimination_check is only valid during the Capture Territory phase')

        terrs = self.data.territories()
        sc_counts = {}
        for tid, t in self.game_state.territories.items():
            if terrs[tid]['type'] != 'land' or not terrs[tid].get('strategic_center'):
                continue
            if t.owner:
                sc_counts[t.owner] = sc_counts.get(t.owner, 0) + 1

        for code, fstate in self.game_state.factions.items():
            if fstate.eliminated or fstate.mode == FactionMode.NEUTRAL:
                continue
            if sc_counts.get(code, 0) <= 1:
                fstate.eliminated = True
                for t in self.game_state.territories.values():
                    t.units = [u for u in t.units if u.owner != code]

    def would_game_end(self):
        """Pure query, no mutation: victory.game_end_rule -- would the
        game be over right now, as-is? True once every remaining active
        faction (GameState.active_factions()) is mutually allied with every
        other -- since FactionState.alliance is a single tag per
        faction, that's exactly equivalent to all of them sharing the
        same one non-None value (trivially true too with 1 or 0 active
        factions left -- nobody remains to still be at war with). A caller
        (a UI) uses this to decide whether it's even worth asking the
        current player about process_game_end_check's one-last-chance
        withdrawal -- nothing to prompt for if the game wasn't about to
        end anyway."""
        active = self.game_state.active_factions()
        if len(active) <= 1:
            return True
        alliances = {self.game_state.factions[code].alliance for code in active}
        return len(alliances) == 1 and None not in alliances

    def process_game_end_check(self, faction, withdraw_from_alliance=False):
        """victory.game_end_rule, checked once at the very end of
        `faction`'s full turn -- after the (stubbed) Alliances phase,
        the last of the 7 turn_order phases. Before the game is declared
        over, `faction` gets one last chance to withdraw from its
        alliance (withdraw_from_alliance=True clears
        FactionState.alliance for `faction` specifically) -- doing so
        keeps the game going if that alliance was the only thing making
        would_game_end() true. This is the ONLY alliance-withdrawal
        action implemented anywhere in the engine; full join/withdraw
        mechanics otherwise remain a v1 stub (see alliances.status).

        Sets GameState.game_over to match the result (would_game_end(),
        evaluated AFTER applying the withdrawal, if any) and returns it.
        Automatic in that it takes no staging/rollback of its own
        (phase_confirmation.scope), but unlike every other automatic
        phase call it DOES take a single yes/no player choice, since
        that choice is the entire point of the 'last chance' rule."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.ALLIANCES:
            raise ValueError('process_game_end_check is only valid during the Alliances phase')

        if withdraw_from_alliance:
            self.game_state.factions[faction].alliance = None

        self.game_state.game_over = self.would_game_end()
        return self.game_state.game_over

    def advance_phase(self):
        """Moves GameState.phase to the next one in turn_order's fixed
        7-phase sequence (Purchase -> Combat Move -> Combat Resolution
        -> Non-Combat Move -> Capture Territory -> Deploy + Income ->
        Alliances). Call this once the active faction's work for the
        current phase is done, before starting the next phase's calls.
        A no-op once Alliances is reached -- call advance_turn() instead
        to close out the faction's turn and move to the next faction's
        Purchase phase."""
        idx = _PHASE_ORDER.index(self.game_state.phase)
        if idx < len(_PHASE_ORDER) - 1:
            self.game_state.phase = _PHASE_ORDER[idx + 1]

    def advance_turn(self):
        """Closes out the currently active faction's turn and opens the
        next one's -- call this once, after process_game_end_check has
        run for the current active_faction (whose full turn, all 7
        phases, is now done).

        Resets that faction's own units' has_moved_combat/
        has_moved_noncombat flags (nothing else in the engine ever does
        -- without this, a unit that moved once could never move again
        for the rest of the game) and clears it from every phase-
        confirmation guard set (_purchases_confirmed and friends --
        without this, a faction could only ever complete each phase
        once, ever, ACROSS THE WHOLE GAME, not once per turn), then
        advances active_faction to the next one in active_factions()
        (wrapping around, and naturally skipping anyone eliminated since
        this faction's turn began, since active_factions() is always
        recomputed fresh), increments global_turn, and resets phase back
        to PURCHASE.

        Raises ValueError if the game is already over or this isn't
        called at the end of the Alliances phase."""
        if self.game_state.game_over:
            raise ValueError('the game is already over')
        if self.game_state.phase != Phase.ALLIANCES:
            raise ValueError('advance_turn is only valid at the end of the Alliances phase')

        finishing = self.game_state.active_faction
        if finishing is not None:
            for t in self.game_state.territories.values():
                for u in t.units:
                    if u.owner == finishing:
                        u.has_moved_combat = False
                        u.has_moved_noncombat = False
            self._purchases_confirmed.discard(finishing)
            self._combat_moves_confirmed.discard(finishing)
            self._combat_resolved.discard(finishing)
            self._noncombat_moves_confirmed.discard(finishing)
            self._return_to_base_processed.discard(finishing)

        active = self.game_state.active_factions()
        if not active:
            self.game_state.active_faction = None
            self.game_state.game_over = True
            return

        next_index = (active.index(finishing) + 1) % len(active) if finishing in active else 0
        self.game_state.active_faction = active[next_index]
        self.game_state.global_turn += 1
        self.game_state.phase = Phase.PURCHASE
