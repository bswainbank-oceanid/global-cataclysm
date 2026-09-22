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
faction's full turn, after the stubbed Diplomacy phase) sets
GameState.game_over once every remaining active faction is mutually
allied with every other -- nobody non-allied left to keep fighting --
but first gives the faction whose turn is ending one last chance to
withdraw from its alliance instead, which keeps the game going; that's
the only alliance-withdrawal action implemented anywhere in the engine,
the rest of alliances remaining a v1 stub.

Orchestration: advance_phase() steps GameState.phase through the fixed
7-phase sequence; advance_turn() (call once, right after
process_game_end_check, at the end of the Diplomacy phase) closes out
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
from .combat import BattleResult, EventKind, resolve_battle, unit_stat_rows
from .economy import compute_income
from .movement import (
    _is_ally_or_self, find_emergency_landing, legal_air_move_destinations,
    legal_combat_move_paths, legal_noncombat_move_destinations, trace_combat_move,
)
from .state import Phase, FactionMode, UnitInstance, is_amphibious
from .turn_log import TurnLog

# turn_order's fixed 7-phase sequence for one faction's full turn.
_PHASE_ORDER = [
    Phase.PURCHASE, Phase.COMBAT_MOVE, Phase.COMBAT_RESOLUTION, Phase.NONCOMBAT_MOVE,
    Phase.CAPTURE, Phase.DEPLOY_INCOME, Phase.DIPLOMACY,
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


# The reasons round1_bonus reports (shown to players on the battle board).
ROUND1_RECLAIM = 'former-ally territory reclaim'
ROUND1_AMBUSH = 'sea-deploy ambush'
ROUND1_AMPHIBIOUS = 'amphibious landing'


class GameEngine:
    def __init__(self, game_state, data_module=None, stats=None, combat_rng=None, turn_log=None):
        self.game_state = game_state
        self.data = data_module or _default_data
        # Optional stats.GameStats observer -- if given, deploys,
        # captures, promotions, deaths, and kills are reported into it as
        # they happen (see stats.py's module docstring). None (the
        # default) means no observation at all; every other behavior
        # here is identical either way.
        self.stats = stats
        # Optional turn_log.TurnLog observer -- an ORDERED narration (not
        # an aggregate, unlike stats above) of purchase/move orders,
        # roll-by-roll combat events, captures, deploys, income, and
        # alliance actions, for a caller (server/session.py) that wants
        # to replay what happened -- a bot's whole turn, or a human's own
        # Combat Resolution -- back to a human audience at their own
        # pace. See turn_log.py's own module docstring.
        self.turn_log = turn_log
        # resolve_combat's dice source when a call doesn't pass its own
        # `rng` (a per-call override, mainly for tests -- ScriptedRNG).
        # Created ONCE here and reused across the WHOLE game -- a real
        # bug until this session: resolve_combat used to fall back to a
        # brand-new, unseeded random.Random() on every single call, so
        # even a fully-seeded setup (build_game_state's rng, every bot's
        # own rng) still produced a different game every run, since
        # combat dice -- the single biggest driver of outcomes -- were
        # never actually part of that seed. Pass an explicit
        # random.Random(seed) here for a genuinely reproducible game;
        # omit it for an unseeded (OS-entropy) one, same as before.
        self._combat_rng = combat_rng or random.Random()
        self._staged_purchases = {}  # faction_code -> [PurchaseOrder, ...]
        self._purchases_confirmed = set()
        self._staged_combat_moves = {}  # faction_code -> [CombatMoveOrder, ...]
        self._combat_moves_confirmed = set()
        self._combat_resolved = set()  # faction_codes that have already run resolve_combat this turn
        self._staged_noncombat_moves = {}  # faction_code -> [NonCombatMoveOrder, ...]
        self._noncombat_moves_confirmed = set()
        self._return_to_base_processed = set()  # faction_codes that have already run process_return_to_base this turn
        self._alliance_action_taken = set()  # faction_codes that have already invited or withdrawn this turn (the Diplomacy phase's "one of two things")

    def _purchase_sources(self, deploy_at, faction):
        """Ordered list of territory_ids whose capacity/cost apply to a
        purchase targeting `deploy_at`: just [deploy_at] if it's a land
        territory `faction` owns (empty list if not -- an illegal
        target); for a sea zone, every adjacent land territory `faction`
        owns and is NOT contested (purchase.contested_land_deploy_restriction
        -- a contested territory's port can't fund power projection out
        into the water any more than it can host anything but Infantry
        deploying onto the land itself), Strategic Center(s) first, then
        by deploy cap descending (ties broken by territory_id for
        determinism) -- see rules.json's purchase.multi_adjacent_allocation_order."""
        terrs = self.data.territories()
        if terrs[deploy_at]['type'] == 'land':
            return [deploy_at] if self.game_state.territories[deploy_at].owner == faction else []
        neighbors = self.data.adjacency().get(deploy_at, [])
        owned_land = [
            tid for tid in neighbors
            if terrs[tid]['type'] == 'land' and self.game_state.territories[tid].owner == faction
            and not self.game_state.territories[tid].contested_by
        ]

        def sort_key(tid):
            terr = terrs[tid]
            is_sc = self.game_state.is_strategic_center(tid, terr)
            cap = terr['value'] + (2 if is_sc else 0)
            return (0 if is_sc else 1, -cap, tid)
        return sorted(owned_land, key=sort_key)

    def legal_purchase_targets(self, faction):
        """(sc_targets, other_targets): every territory `faction` could
        legally purchase at (per _purchase_sources -- a land territory it
        owns, or a sea zone adjacent to at least one of its owned land
        territories), split by whether a Strategic Center's capacity/cost
        is actually in play there. A sea target counts as an SC target if
        ANY of its eligible sources (_purchase_sources -- SC-first) is a
        Strategic Center. A pure query -- takes no action, so any caller
        (a UI wanting to show legal choices before the player composes an
        order, or a bot deciding where to shop) can use it; RandomBot
        used to have its own private copy of exactly this logic before it
        moved here this session."""
        terrs = self.data.territories()
        adjacency = self.data.adjacency()
        gs = self.game_state

        owned_land = [tid for tid, t in gs.territories.items() if terrs[tid]['type'] == 'land' and t.owner == faction]
        sc_targets = {tid for tid in owned_land if gs.is_strategic_center(tid, terrs[tid])}
        other_targets = set(owned_land) - sc_targets

        sea_candidates = set()
        for tid in owned_land:
            for n in adjacency.get(tid, []):
                if terrs[n]['type'] == 'sea':
                    sea_candidates.add(n)
        for sea_tid in sea_candidates:
            sources = self._purchase_sources(sea_tid, faction)
            if not sources:
                continue  # every adjacent owned territory is contested (or otherwise ineligible) -- no legal target
            if any(gs.is_strategic_center(s, terrs[s]) for s in sources):
                sc_targets.add(sea_tid)
            else:
                other_targets.add(sea_tid)

        return list(sc_targets), list(other_targets)

    def _deploy_cap(self, territory_id):
        terr = self.data.territories()[territory_id]
        return terr['value'] + (2 if self.game_state.is_strategic_center(territory_id, terr) else 0)

    def _unit_cost(self, unit_type, territory_id):
        terr = self.data.territories()[territory_id]
        unit_def = self.data.units()[unit_type]
        return unit_def['sc_cost'] if self.game_state.is_strategic_center(territory_id, terr) else unit_def['cost']

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
            elif unit_def['category'] == 'Land' and not is_amphibious(unit_def):
                raise ValueError(f'{order.unit_type} cannot deploy to a sea space ({deploy_at}); only Mechanized Infantry can')

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
        if self.turn_log is not None:
            self.turn_log.record_purchase(faction, orders, total_cost)

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
        income = compute_income(faction, self.game_state, self.data)
        self.game_state.factions[faction].treasury_mpc += income
        if self.stats is not None:
            self.stats.record_income(faction, income)
        if self.turn_log is not None:
            self.turn_log.record_income(faction, income)
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
            self._record_deploys(faction, tid, pending)
            return
        # Lost during the turn (only ever Infantry -- the only unit
        # type contested_land_deploy_restriction lets into a contested
        # territory in the first place, and Infantry cannot enter the
        # water) -- purchase.contested_purchase_lost_during_turn_fallback.
        fallback = self._find_fallback_for_lost_purchase(tid, faction)
        if fallback is None:
            return  # no adjacent controlled territory -- lost outright, never placed
        self.game_state.territories[fallback].units.extend(pending)
        self._record_deploys(faction, fallback, pending)

    def _record_deploys(self, faction, territory_id, units):
        if self.stats is not None:
            for u in units:
                self.stats.record_deploy(faction, u.unit_type)
        if self.turn_log is not None:
            by_type = {}
            for u in units:
                by_type[u.unit_type] = by_type.get(u.unit_type, 0) + 1
            for unit_type, qty in by_type.items():
                self.turn_log.record_deploy(faction, territory_id, unit_type, qty)

    def _find_fallback_for_lost_purchase(self, tid, faction):
        """An adjacent territory `faction` still controls, or None (the
        units are lost) -- the water is no fallback: the units are Infantry,
        which cannot enter it. No tie-break order is specified for multiple
        qualifying options, so this picks deterministically -- the lowest
        territory_id."""
        terrs = self.data.territories()
        neighbors = self.data.adjacency().get(tid, [])
        controlled_land = sorted(
            n for n in neighbors if terrs[n]['type'] == 'land' and self.game_state.territories[n].owner == faction
        )
        return controlled_land[0] if controlled_land else None

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
        redirected_by_land = {}
        for u in pending:
            if unit_defs[u.unit_type]['category'] == 'Air' and not has_own_carrier:
                self.game_state.territories[u.purchased_at].units.append(u)
                redirected_by_land.setdefault(u.purchased_at, []).append(u)
            else:
                to_place_here.append(u)
        t.units.extend(to_place_here)
        # Both the redirected-to-land and placed-here units actually
        # landed on the board -- recorded separately since they may not
        # share a single territory_id (each redirected unit goes back to
        # its OWN purchasing land space, not necessarily all the same one).
        if to_place_here:
            self._record_deploys(faction, tid, to_place_here)
        for land_tid, units in redirected_by_land.items():
            self._record_deploys(faction, land_tid, units)

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
            # combat.first_round_bonuses' "sea deploy into enemy-occupied
            # zone" case -- every prior non-ally caught here was equally
            # unprepared for `faction`'s deploy (confirmed this session:
            # more than one can be queued at once, each independently
            # consumed on their own next attack here).
            t.ambush_bonus_for |= non_allies

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

    def legal_combat_move_options(self, faction, game_state=None):
        """{unit_id: {'unit_type': ..., 'territory_id': origin_id,
        'destinations': {destination_id: path, ...}}} for every one of
        `faction`'s own units, anywhere on the board, that hasn't already
        combat-moved this turn (UnitInstance.has_moved_combat) -- known
        at the start of Combat Move, before any order is submitted (a
        later order in the same submission CAN change what's legal for a
        unit considered after it -- _execute_combat_moves' own docstring
        -- so this is a snapshot as of right now, not a guarantee that
        stays valid after the caller's own earlier picks; submit_combat_
        moves is still the authority). `path` always includes both
        endpoints (`[origin_id, destination_id]` at minimum), same shape
        CombatMoveOrder.path expects -- for an Air unit that's always
        exactly 2 entries (no hop-by-hop legality for air), for a Land or
        Sea unit it may be longer (an uncontested Mechanized Infantry
        blitz, or a multi-hop path generally). A pure query -- takes no
        action; a unit with zero legal destinations (nothing to attack,
        nowhere to go) is simply omitted, not included with an empty
        dict. No bot-only policy exclusions applied here (e.g. RandomBot
        never moves an SC-garrisoning Infantry) -- those are strategy
        choices, not engine-level illegality; see engine.bots.random_bot
        for that layer."""
        gs = game_state or self.game_state  # a working copy, e.g. with staged moves applied
        terrs = self.data.territories()
        unit_defs = self.data.units()
        options = {}
        for tid, t in gs.territories.items():
            for u in t.units:
                if u.owner != faction or u.has_moved_combat:
                    continue
                category = unit_defs[u.unit_type]['category']
                if category == 'Air':
                    legal = legal_air_move_destinations(u.unit_type, faction, tid, 'combat', gs, self.data)
                    destinations = {dest: [tid, dest] for dest in legal}
                else:
                    destinations = legal_combat_move_paths(u.unit_type, faction, tid, gs, self.data)
                if not destinations:
                    continue
                options[u.unit_id] = {'unit_type': u.unit_type, 'territory_id': tid, 'destinations': destinations}
        return options

    def legal_noncombat_move_options(self, faction, game_state=None):
        """{unit_id: {'unit_type': ..., 'territory_id': origin_id,
        'destinations': [destination_id, ...]}} for every one of
        `faction`'s own units still eligible to make a non-combat move --
        has_moved_noncombat is False, and (unless it's an Air unit,
        exempt from combat_or_noncombat_not_both) has_moved_combat is
        also False. Meant to be queried AFTER Combat Resolution has
        actually run and, for a human, after process_return_to_base's
        automatic snap-back has already claimed every air unit it can
        (that landing REPLACES the unit's non-combat move -- see
        process_return_to_base -- so those units are naturally excluded
        here too, same as any other already-moved unit): what's legal
        genuinely depends on how combat played out (captures,
        eliminations, newly contested territory), not just where things
        stood at the start of the turn -- unlike legal_combat_move_
        options, there's no meaningful "known at turn start" snapshot to
        take here.

        Unlike legal_combat_move_options, `destinations` is a plain,
        sorted list of territory ids, not {destination: path} --
        NonCombatMoveOrder only ever needs the endpoint (no blitz/route
        concept off the combat-move phase; see that dataclass's own
        docstring), so there's no route to report. A unit with zero
        legal destinations is simply omitted, same convention as
        legal_combat_move_options.

        Deliberately does NOT special-case or omit an Air unit just
        because every one of its remaining legal destinations would
        strand it -- per movement.stranded_aircraft_rule, an aircraft
        left over open water with no own carrier at phase end is simply
        lost, and the engine already only enforces that actual loss
        (_apply_stranded_aircraft_check), never blocks declaring the
        move that leads to it. Warning a player before they submit one
        is a client UI concern, not an engine-level restriction. No
        bot-only policy exclusions applied here either, same rationale
        as legal_combat_move_options."""
        gs = game_state or self.game_state  # a working copy, e.g. with staged moves applied
        unit_defs = self.data.units()
        options = {}
        for tid, t in gs.territories.items():
            for u in t.units:
                if u.owner != faction or u.has_moved_noncombat:
                    continue
                category = unit_defs[u.unit_type]['category']
                if category == 'Air':
                    legal = legal_air_move_destinations(u.unit_type, faction, tid, 'noncombat', gs, self.data)
                else:
                    if u.has_moved_combat:
                        continue
                    legal = legal_noncombat_move_destinations(u.unit_type, faction, tid, gs, self.data)
                if not legal:
                    continue
                options[u.unit_id] = {'unit_type': u.unit_type, 'territory_id': tid, 'destinations': sorted(legal)}
        return options

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
                if category == 'Land':
                    # combat.first_round_bonuses' amphibious-landing check
                    # (resolve_combat) -- stamped fresh on every land
                    # unit's own combat move, overwriting whatever was
                    # left from an earlier turn either way.
                    unit.arrived_amphibiously = trace.crossed_water
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
        unit_info = self._unit_info(o.unit_id for o in orders) if self.turn_log is not None else None
        self._execute_combat_moves(orders, faction, self.game_state)
        self._combat_moves_confirmed.add(faction)
        self._staged_combat_moves.pop(faction, None)
        if self.turn_log is not None:
            self.turn_log.record_combat_move(faction, orders, unit_info)

    def _unit_info(self, unit_ids):
        """{unit_id: (unit_type, territory_id)} for each of `unit_ids`
        currently on the board -- captured BEFORE a move list executes, for
        TurnLog's per-order "where did it start" record. Ids that aren't on
        the board (an invalid order) are simply absent."""
        wanted = set(unit_ids)
        info = {}
        for tid, t in self.game_state.territories.items():
            for u in t.units:
                if u.unit_id in wanted:
                    info[u.unit_id] = (u.unit_type, tid)
        return info

    # ---- Queued-orders previews -----------------------------------------
    # What a phase WILL do, in the same event shape TurnLog records once it
    # has, built without touching the log or the game -- for a watcher that
    # shows a bot's staged orders before they're confirmed.

    def staged_purchase_event(self, faction):
        orders = self._staged_purchases.get(faction, [])
        total_cost, _ = self._resolve_and_cost(orders, faction)
        scratch = TurnLog()
        scratch.record_purchase(faction, orders, total_cost)
        return scratch.events[0]

    def purchase_options(self, faction):
        """What a purchase UI needs beyond legal_purchase_targets, given the
        currently STAGED purchases: for every legal target, how many more units
        it can still take ('remaining', across the eligible source territories),
        and whether the NEXT unit bought there would be charged at a Strategic
        Center's discounted price ('next_sc'); plus 'orders' -- each staged order
        with its exact cost and which territories' capacity paid for it -- and
        the staged 'total_cost'. A pure query."""
        orders = self._staged_purchases.get(faction, [])
        total_cost, allocations = self._resolve_and_cost(orders, faction)
        consumed = {}
        for alloc in allocations:
            for source, qty in alloc:
                consumed[source] = consumed.get(source, 0) + qty
        terrs = self.data.territories()
        sc_targets, other_targets = self.legal_purchase_targets(faction)
        targets = {}
        for tid in sc_targets + other_targets:
            sources = self._purchase_sources(tid, faction)
            left = [(s, self._deploy_cap(s) - consumed.get(s, 0)) for s in sources]
            nxt = next((s for s, n in left if n > 0), None)
            targets[tid] = {
                'remaining': sum(max(n, 0) for _, n in left),
                'next_sc': bool(nxt is not None and self.game_state.is_strategic_center(nxt, terrs[nxt])),
                'sources': list(sources),
            }
        detail = []
        for order, alloc in zip(orders, allocations):
            detail.append({
                'unit_type': order.unit_type, 'qty': order.qty, 'deploy_at': order.deploy_at,
                'cost': sum(qty * self._unit_cost(order.unit_type, src) for src, qty in alloc),
                'allocation': [[src, qty] for src, qty in alloc],
            })
        contested = [tid for tid in targets
                     if terrs[tid]['type'] == 'land' and self.game_state.territories[tid].contested_by]
        return {
            'treasury': self.game_state.factions[faction].treasury_mpc,
            'total_cost': total_cost, 'targets': targets, 'orders': detail, 'contested': contested,
        }

    def move_options_with_staged(self, faction, kind):
        """What is still legal for `faction`'s units once the moves staged so far
        ('combat' or 'noncombat') are applied: the same shape as
        legal_combat_move_options / legal_noncombat_move_options, computed on a
        throwaway copy where the staged units have already moved (so they no
        longer appear, and a newly contested territory changes what the rest may
        do). A pure query -- what a move UI needs after every staged change."""
        working = copy.deepcopy(self.game_state)
        if kind == 'combat':
            self._execute_combat_moves(self._staged_combat_moves.get(faction, []), faction, working)
            return self.legal_combat_move_options(faction, working)
        self._execute_noncombat_moves(self._staged_noncombat_moves.get(faction, []), faction, working)
        return self.legal_noncombat_move_options(faction, working)

    def staged_moves_detail(self, faction, kind):
        """The staged moves as a UI wants them: one dict per ordered unit, with its
        type and where it starts ('from'), plus its 'path' (combat) or
        'destination' (non-combat)."""
        orders = (self._staged_combat_moves if kind == 'combat' else self._staged_noncombat_moves).get(faction, [])
        info = self._unit_info(o.unit_id for o in orders)
        out = []
        for o in orders:
            unit_type, origin = info.get(o.unit_id, (None, None))
            entry = {'unit_id': o.unit_id, 'unit_type': unit_type, 'from': origin}
            if kind == 'combat':
                entry['path'] = list(o.path)
            else:
                entry['destination'] = o.destination
            out.append(entry)
        return out

    def staged_combat_move_event(self, faction):
        orders = self._staged_combat_moves.get(faction, [])
        scratch = TurnLog()
        scratch.record_combat_move(faction, orders, self._unit_info(o.unit_id for o in orders))
        return scratch.events[0]

    def staged_noncombat_move_event(self, faction):
        orders = self._staged_noncombat_moves.get(faction, [])
        scratch = TurnLog()
        scratch.record_noncombat_move(faction, orders, self._unit_info(o.unit_id for o in orders))
        return scratch.events[0]

    def battle_preview(self, faction, territory_id, battle_type):
        """A 'battle_preview' event for one battle resolve_one_battle is about
        to fight: who is on each side, with the numbers a battle board places
        them by -- attack die, defense (Dig In counted for the defending
        side), HP, XP, promotion, and `cargo` for a land unit that is Transport
        cargo in a sea battle. (The dice haven't been rolled, so no outcome.
        Round-specific bonuses -- air superiority, first-round -- arrive with
        the battle's UNIT_STATS events instead.)"""
        attackers, defenders = self.gather_battle_units(territory_id, faction)
        unit_defs = self.data.units()

        def rows(side, units):
            out = []
            for u in units:
                cargo = battle_type == 'sea' and unit_defs[u.unit_type]['category'] == 'Land'
                u.in_transport_form = cargo  # stats as the battle will use them; undone below
                try:
                    out.extend(unit_stat_rows(side, [u], unit_defs))
                finally:
                    u.in_transport_form = False
                if cargo:
                    out[-1]['hp'] = out[-1]['max_hp']  # a Transport starts at full HP
            return out

        side, reason = self.round1_bonus(faction, territory_id, battle_type, attackers, defenders)
        return {
            'kind': 'battle_preview', 'territory_id': territory_id, 'battle_type': battle_type,
            'attackers': rows('attacker', attackers),
            'defenders': rows('defender', defenders),
            'round1_bonus': {'side': side, 'reason': reason},
        }

    def battle_previews(self, faction):
        """One battle_preview per battle resolve_combat(faction) is about to
        fight, in resolution order."""
        return [self.battle_preview(faction, tid, bt) for tid, bt in self.declared_battles(faction)]

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

        combat.first_round_bonuses: all three cases resolved here, in
        priority order (a battle only ever gets one):
        1. Former-ally-territory-reclaim (land only, RECURRING -- confirmed
           this session, a change from this bonus's original one-shot
           design): if this territory's TerritoryState.reclaim_bonus_for
           names `faction` (set by an earlier withdraw_from_alliance, when
           this territory, belonging to `faction`, was left occupied by
           the withdrawing faction), `faction` gets the round-1 bonus
           EVERY qualifying battle here for as long as the flag stays
           set -- as ATTACKER on `faction`'s own turn, or as DEFENDER on
           anyone else's turn if `faction` still has units here (e.g. the
           betrayer attacking `faction`'s freshly marched-in Infantry).
           NOT cleared here anymore -- only process_capture_territory
           clears it, once the contest is actually resolved one way or
           the other (see combat.true_territory_loss's betrayal
           exception in _apply_battle_outcome, which keeps this contest
           alive across `faction`'s repeated attempts instead of handing
           the territory away the first time one fails).
        2. Sea-deploy-ambush (sea only): if `faction` is in this
           territory's TerritoryState.ambush_bonus_for (set by an earlier
           _deploy_to_sea, when `faction` already had units in a sea zone
           a non-ally then deployed into), `faction`'s attack gets the
           round-1 bonus, and just `faction` is removed from that set --
           any OTHER still-queued faction's own entry is untouched.
        3. Amphibious landing (land only, one-shot -- confirmed this
           session): if EVERY Land-category unit currently on the
           attacking side both combat-moved THIS turn
           (UnitInstance.has_moved_combat) and did so via a path that
           touched a sea zone (UnitInstance.arrived_amphibiously, stamped
           by _execute_combat_moves from movement.trace_combat_move's
           crossed_water), the DEFENDER gets the round-1 bonus instead --
           nothing to clear here, it's recomputed fresh from unit state
           every call, never queued. A mid-battle refight of an ongoing
           multi-turn stalemate (no fresh combat move this turn) never
           qualifies, even if the same units are still sitting there,
           since has_moved_combat is false for anyone who didn't just
           move -- this is deliberately NOT a "this unit has ever crossed
           water and hasn't walked since" persistent tracker.

        Not reversible (phase_confirmation.scope: dice have already been
        rolled) -- there's no staging here, this is the real thing the
        moment it's called. Can only be called once per faction per turn
        (a second call would otherwise re-fight any still-contested
        standoff a second time within the same phase).

        `rng` overrides self._combat_rng for just this one call (tests
        use this with a ScriptedRNG); leave it out to draw from the
        engine's own persistent combat_rng stream instead."""
        self.begin_combat_resolution(faction)
        return [
            self.resolve_one_battle(faction, territory_id, battle_type, rng)
            for territory_id, battle_type in self.declared_battles(faction)
        ]

    def begin_combat_resolution(self, faction):
        """The once-per-turn gate resolve_combat goes through first (and what
        a caller resolving battles one at a time via resolve_one_battle --
        the watch-mode stepper -- calls before the first one): validates the
        phase and marks `faction`'s Combat Resolution as started, so a second
        resolve_combat this turn is refused."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.COMBAT_RESOLUTION:
            raise ValueError('resolve_combat is only valid during the Combat Resolution phase')
        if faction in self._combat_resolved:
            raise ValueError(f'{faction} has already resolved combat this turn')
        self._combat_resolved.add(faction)

    def round1_bonus(self, faction, territory_id, battle_type, attacker_units, defender_units):
        """(side, reason) for combat.first_round_bonuses in the battle `faction`
        is about to fight at `territory_id`: which side ('attacker' |
        'defender', or None) gets it and why (a ROUND1_* text, or None). A pure
        query -- resolve_one_battle spends the one-shot ambush flag itself --
        so a battle board can say up front who has the bonus. Priority: a
        former-ally reclaim, then a sea-deploy ambush, then an amphibious
        landing (a battle only ever gets one)."""
        t = self.game_state.territories[territory_id]
        unit_defs = self.data.units()
        if t.reclaim_bonus_for == faction:
            return 'attacker', ROUND1_RECLAIM
        if t.reclaim_bonus_for is not None and any(u.owner == t.reclaim_bonus_for for u in defender_units):
            return 'defender', ROUND1_RECLAIM
        if faction in t.ambush_bonus_for:
            return 'attacker', ROUND1_AMBUSH
        if battle_type == 'land':
            land_attackers = [u for u in attacker_units if unit_defs[u.unit_type]['category'] == 'Land']
            if land_attackers and all(u.has_moved_combat and u.arrived_amphibiously for u in land_attackers):
                return 'defender', ROUND1_AMPHIBIOUS
        return None, None

    def resolve_one_battle(self, faction, territory_id, battle_type, rng=None):
        """Fights ONE of `faction`'s declared_battles (see resolve_combat's
        docstring for the bonuses and outcome handling), applies its
        consequences, and returns its BattleResult. resolve_combat is
        begin_combat_resolution plus this for each declared battle, in order;
        a caller pacing battles itself does the same, calling
        begin_combat_resolution once first."""
        rng = rng or self._combat_rng
        unit_defs = self.data.units()
        rules = self.data.rules()
        attacker_units, defender_units = self.gather_battle_units(territory_id, faction)
        t = self.game_state.territories[territory_id]

        round1_bonus_side, bonus_reason = self.round1_bonus(faction, territory_id, battle_type, attacker_units, defender_units)
        if bonus_reason == ROUND1_AMBUSH:
            t.ambush_bonus_for.discard(faction)  # one-shot: spent by fighting this battle

        events = list(resolve_battle(
            attacker_units, defender_units, battle_type, rng,
            self.game_state.global_turn, unit_defs, rules,
            round1_bonus_side=round1_bonus_side,
        ))
        result = BattleResult.from_events(events)
        self._record_combat_stats(events, attacker_units, defender_units, battle_type)
        if self.turn_log is not None:
            self.turn_log.record_battle_events(territory_id, battle_type, events, attacker_units, defender_units)
        self._apply_battle_outcome(territory_id, battle_type, faction, result, rng)
        return result

    def _record_combat_stats(self, events, attacker_units, defender_units, battle_type):
        """Promotions and kills come straight off the event stream
        (PROMOTION events, and any UNIT_ROLL hit that drops its target's
        target_hp_after to <=0 -- a "killing blow", re-derived here from the
        public events). Deaths come off the final
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

        if self.stats is not None:
            # stats.rounds_contested: this counts as one more round of
            # contest over this territory, regardless of the outcome --
            # consumed by process_capture_territory's record_capture if
            # a capture eventually resolves it, or cleared below/there if
            # the contest ends some other way.
            self.stats.record_battle_resolved(territory_id)

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
            #
            # combat.true_territory_loss (this session): attacker_always_
            # solo labels `faction` "attacker" here even when it's really
            # just DEFENDING its own ground against a standing contest
            # (an enemy attacked on an earlier turn and it's `faction`'s
            # OWN Combat Resolution phase re-fighting the stalemate) --
            # confirmed by the user: "the clearest path to being
            # eliminated on your turn is an enemy contesting your SC...
            # your forces might lose... this can lead to a true loss of
            # territory."
            #
            # EXCEPT for an alliance-betrayal reclaim in progress
            # (TerritoryState.reclaim_bonus_for == faction, set by an
            # earlier withdraw_from_alliance): confirmed by the user --
            # "having troops on enemy lands is an exception to
            # transferring ownership... the contestation can only be
            # cleared during the betrayer's [own] claim territory phase.
            # Until then, the original owner can marshal infantry there,
            # [and] do both combat and non-combat moves into the
            # territory." So a single failed counter-attack must NOT hand
            # the territory to the betrayer -- contested_by is left
            # completely untouched here (still naming both sides), so
            # `faction` keeps getting fresh chances (with the reclaim
            # bonus, see resolve_combat) on later turns, and the betrayer
            # can only actually finish claiming it via the ordinary
            # process_capture_territory path on the betrayer's OWN turn,
            # same as any other territory that faction has zero presence
            # in when that runs.
            if battle_type == 'land' and t.reclaim_bonus_for == faction:
                return

            new_owner = None
            if battle_type == 'land' and t.owner == faction:
                new_owner = self._true_territory_loss_winner(t, faction)
            t.contested_by = None
            # If `faction` here is the BETRAYER, and its own attack on
            # the original owner's counter-defense just failed outright
            # (t.owner was never flipped to it in the first place, so
            # neither branch above fires), the reclaim contest has still
            # genuinely ended -- the original owner keeps the territory
            # free and clear. Whatever pending reclaim_bonus_for this
            # territory had is no longer meaningful either way once
            # contested_by clears here, so it's cleared alongside it --
            # process_capture_territory clears it in the two cases THAT
            # resolves the contest instead; this covers the third, where
            # combat resolution itself is what ends it.
            t.reclaim_bonus_for = None
            if new_owner is not None:
                previous_owner = t.owner
                t.owner = new_owner
                if self.stats is not None:
                    self.stats.record_capture(self.game_state.global_turn, new_owner, territory_id, previous_owner)
                if self.turn_log is not None:
                    self.turn_log.record_capture(self.game_state.global_turn, new_owner, territory_id, previous_owner)
            elif self.stats is not None:
                self.stats.record_contest_ended_without_capture(territory_id)
            return

        if battle_type == 'sea' and result.outcome != 'contested':
            # Sea has no Capture Territory equivalent to later resolve a
            # decisive win into an ownership change -- once the fight
            # itself is over (not a 3-round stalemate still to refight),
            # there's nothing left here to track.
            t.contested_by = None
            if self.stats is not None:
                self.stats.record_contest_ended_without_capture(territory_id)
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

    def _true_territory_loss_winner(self, territory_state, faction):
        """Called only when `faction` just lost its OWN territory
        outright (see combat.true_territory_loss in _apply_battle_outcome
        above) -- who actually takes it. Every LAND unit still present
        that ISN'T `faction` or one of its allies is pooled by owner
        (combat.multi_party_battles: could be more than one distinct
        non-allied faction at once), and the one with the greatest TOTAL
        cost of its land units here wins; ties broken by turn order
        (GameState.factions' fixed iteration order -- earlier wins).
        None if nobody has land presence at all (air-only survivors, or a
        mutual wipe) -- the ground is simply abandoned, not captured;
        ownership stays with `faction` by default, same standard
        _determine_capture_winner uses for its own "nobody's here"
        fallback (air can't capture, and nobody to hand it to either)."""
        unit_defs = self.data.units()
        by_owner = {}
        for u in territory_state.units:
            if unit_defs[u.unit_type]['category'] != 'Land':
                continue
            if _is_ally_or_self(self.game_state, faction, u.owner):
                continue
            by_owner.setdefault(u.owner, []).append(u)
        if not by_owner:
            return None

        turn_order = list(self.game_state.factions.keys())

        def sort_key(owner):
            total_cost = sum(unit_defs[u.unit_type]['cost'] or 0 for u in by_owner[owner])
            return (total_cost, -turn_order.index(owner))

        return max(by_owner, key=sort_key)

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

    def has_processed_return_to_base(self, faction):
        """True once process_return_to_base has run for `faction` this turn."""
        return faction in self._return_to_base_processed

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
        moved = []
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
                moved.append((unit.unit_id, unit.unit_type, current_id, target))
            unit.has_moved_noncombat = True
        if moved and self.turn_log is not None:
            self.turn_log.record_return_to_base(faction, moved)

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
        unit_info = self._unit_info(o.unit_id for o in orders) if self.turn_log is not None else None
        self._execute_noncombat_moves(orders, faction, self.game_state)
        self._apply_stranded_aircraft_check(faction)
        self._noncombat_moves_confirmed.add(faction)
        self._staged_noncombat_moves.pop(faction, None)
        if self.turn_log is not None:
            self.turn_log.record_noncombat_move(faction, orders, unit_info)

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
            # combat.true_territory_loss's betrayal exception: this is
            # one of the two ways an alliance-reclaim contest actually
            # concludes (the other is inside _apply_battle_outcome, when
            # the betrayer's own attack fails outright) -- whoever just
            # won the ground here, the pending reclaim is no longer
            # meaningful either way.
            t.reclaim_bonus_for = None
            if self.stats is not None:
                if previous_owner != t.owner:
                    self.stats.record_capture(self.game_state.global_turn, t.owner, tid, previous_owner)
                else:
                    # Same owner reclaims/reaffirms its own ground -- not
                    # a capture (no report entry), but the contest is
                    # still over, so the rounds-contested counter must
                    # be cleared here too, same as record_capture would
                    # have consumed it.
                    self.stats.record_contest_ended_without_capture(tid)
            if self.turn_log is not None and previous_owner != t.owner:
                self.turn_log.record_capture(self.game_state.global_turn, t.owner, tid, previous_owner)

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

    # ---- surrender (victory.surrender_rule) -----------------------------------
    # A faction is no longer eliminated automatically for being down to 0-1 Strategic
    # Centers. It is eliminated when another faction forces its surrender in the
    # Diplomacy phase, which needs one of two grounds (surrender_grounds).

    def _controlled_scs(self, faction):
        """Territory ids of the Strategic Centers `faction` currently owns (a contested one
        still counts: ownership only changes when the contest resolves)."""
        terrs = self.data.territories()
        gs = self.game_state
        return [tid for tid, t in gs.territories.items()
                if t.owner == faction and terrs[tid]['type'] == 'land' and gs.is_strategic_center(tid, terrs[tid])]

    def surrender_grounds(self, demander, target):
        """Why `demander` may force `target`'s surrender right now: a list with 'income' (the
        demander's income -- the value of its uncontested territories -- is more than 200% of the
        target's) and/or 'strategic_center' (the target holds 0-1 Strategic Centers and the demander
        controls a Strategic Center that was originally the target's). Empty when it may not. Pure
        query; both must be factions still in play."""
        gs = self.game_state
        active = gs.active_factions()
        if demander == target or demander not in active or target not in active:
            return []
        reasons = []
        if compute_income(demander, gs, self.data) > 2 * compute_income(target, gs, self.data):
            reasons.append('income')
        if len(self._controlled_scs(target)) <= 1:
            terrs = self.data.territories()
            if any(terrs[tid].get('faction') == target for tid in self._controlled_scs(demander)):
                reasons.append('strategic_center')
        return reasons

    def legal_surrender_targets(self, faction):
        """[{'target', 'reasons', 'allied', 'income': {'yours', 'theirs'}}] -- every other faction
        `faction` may force to surrender right now (allies included: whether to demand an ally's
        surrender is the caller's choice; a bot never does, except to win outright)."""
        gs = self.game_state
        out = []
        members = self._alliance_members(faction)
        for code in gs.active_factions():
            reasons = self.surrender_grounds(faction, code)
            if reasons:
                out.append({'target': code, 'reasons': reasons, 'allied': code in members,
                            'income': {'yours': compute_income(faction, gs, self.data),
                                       'theirs': compute_income(code, gs, self.data)}})
        return out

    def demand_surrender(self, faction, target):
        """Diplomacy phase, any number of times a turn (before or after the alliance action): forces
        `target` to surrender -- it is eliminated at once, its units leave the board. Raises ValueError
        when it is not `faction`'s Diplomacy phase or neither ground holds. Returns the reasons."""
        gs = self.game_state
        if faction not in gs.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if gs.phase != Phase.DIPLOMACY or gs.active_faction != faction:
            raise ValueError('a surrender can only be demanded during the demander\'s own Diplomacy phase')
        if target not in gs.active_factions():
            raise ValueError(f'{target} is not a faction in play')
        if target == faction:
            raise ValueError('a faction cannot demand its own surrender')
        reasons = self.surrender_grounds(faction, target)
        if not reasons:
            raise ValueError(f'{faction} has no grounds to demand the surrender of {target}')
        if self.turn_log is not None:
            self.turn_log.record_surrender(gs.global_turn, gs.round_number, faction, target, reasons)
        self._eliminate(target)
        return reasons

    def surrender(self, faction):
        """The Settings 'Surrender' action: `faction` eliminates itself, of its own accord -- not
        demanded by anyone, and not gated to its own turn or the Diplomacy phase at all, since this
        isn't a move being made IN the game, it's a player choosing to stop playing. Same effect as
        being forced to surrender (demand_surrender): its units and pending purchases leave the board,
        its alliance ties are cut, its territory stays as it is. Checks the game-end condition right
        away (would_game_end()) rather than waiting for the next Diplomacy phase's
        process_game_end_check, since this can happen at any moment -- e.g. it may leave only one
        faction standing, or leave everyone left mutually allied. Raises ValueError if the game is
        already over, there is no such faction, its mode isn't HUMAN/BOT, or it is already eliminated."""
        gs = self.game_state
        if gs.game_over:
            raise ValueError('the game is already over')
        if faction not in gs.factions:
            raise ValueError(f'no such faction: {faction}')
        fstate = gs.factions[faction]
        if fstate.mode not in (FactionMode.HUMAN, FactionMode.BOT):
            raise ValueError(f'{faction} is not a HUMAN or BOT faction')
        if fstate.eliminated:
            raise ValueError(f'{faction} is already eliminated')
        if self.turn_log is not None:
            self.turn_log.record_self_surrender(gs.global_turn, gs.round_number, faction)
        self._eliminate(faction)
        gs.game_over = self.would_game_end()

    def end_by_armistice(self, proposer, participants):
        """The Settings 'Propose Armistice' action, once every other active faction has accepted it
        (a bot always does; a human is asked): ends the game right here, immediately -- not one of the
        ordinary ways a game ends (victory.game_end_rule), and not gated to any phase, since a proposal
        can be made from anywhere. `participants`: every faction whose agreement made this happen (the
        proposer, plus everyone who accepted) -- purely for the record (turn_log/the Game Over report);
        nobody here is a winner or a loser. Raises ValueError if the game is already over."""
        if self.game_state.game_over:
            raise ValueError('the game is already over')
        self.game_state.game_over = True
        if self.turn_log is not None:
            self.turn_log.record_armistice(self.game_state.global_turn, proposer, sorted(participants))

    def _eliminate(self, code):
        """Takes `code` out of the game: eliminated, its units (and purchases waiting to deploy)
        gone from the board, and its alliance ties cut -- an alliance left with one member is no
        alliance at all. Its territory stays as it is. active_factions() excludes it from now on,
        which is what actually ends its turns."""
        gs = self.game_state
        fstate = gs.factions[code]
        fstate.eliminated = True
        for t in gs.territories.values():
            t.units = [u for u in t.units if u.owner != code]
            t.pending_deployment = [u for u in t.pending_deployment if u.owner != code]
        tag, fstate.alliance = fstate.alliance, None
        if tag is not None:
            left = [f for f in gs.factions.values() if f.alliance == tag and not f.eliminated]
            if len(left) <= 1:
                for f in left:
                    f.alliance = None
        if self.turn_log is not None:
            self.turn_log.record_elimination(code)

    def _alliance_members(self, faction):
        """{faction} ∪ every other faction currently sharing its
        alliance tag -- just {faction} alone if it isn't allied at all.
        Pure query."""
        tag = self.game_state.factions[faction].alliance
        if tag is None:
            return {faction}
        return {code for code, f in self.game_state.factions.items() if f.alliance == tag}

    def legal_alliance_options(self, faction):
        """{'eligible_invite_targets': [faction_code, ...], 'can_withdraw':
        bool} for `faction`'s one optional Alliances-phase action this
        turn -- invite OR withdraw, never both (see invite_to_alliance/
        withdraw_from_alliance's shared _alliance_action_taken guard);
        doing neither ('none') is always legal too and needs no entry
        here, same as leaving a unit out of a Purchase/Combat Move/Non-
        Combat Move order list.

        eligible_invite_targets: every active faction that isn't already
        one of `faction`'s own allies, isn't already in SOME alliance of
        its own (it would have to withdraw first, on its own turn), and,
        when can_rejoin_alliances is False, isn't former_allies-banned
        against anyone already in `faction`'s alliance. Deliberately does
        NOT additionally filter by _effective_max_alliance_size -- unlike
        a unit's destination list, which target the caller will actually
        try isn't known yet, so every structurally-eligible target is
        offered; invite_to_alliance is still the authoritative check and
        will reject a specific pick that would exceed the effective max.

        can_withdraw: True only if `faction` is currently in an alliance,
        game_start_settings.can_withdraw_from_alliances is True, and it
        isn't blocked by the design doc's SC lock (a unit of `faction`'s
        own standing on an ally's Strategic Center) -- mirrors
        withdraw_from_alliance's own validation, read-only, so a client
        can grey out that option rather than discovering the rejection
        only after submitting it.

        engine.bots.alliance_policy._eligible_invite_targets delegates to
        this for its own candidate pool (previously its own private copy
        of this same filter) -- any caller, not just a bot, can use it
        now, same reasoning as legal_purchase_targets' own move here
        earlier this project."""
        gs = self.game_state
        members = self._alliance_members(faction)
        targets = []
        for code in gs.active_factions():
            if code in members:
                continue
            if gs.factions[code].alliance is not None:
                continue
            if not gs.can_rejoin_alliances and gs.factions[code].former_allies & members:
                continue
            targets.append(code)

        fstate = gs.factions[faction]
        can_withdraw = (
            fstate.alliance is not None
            and gs.can_withdraw_from_alliances
            and not self._faction_has_units_on_an_allied_sc(faction)
        )
        return {'eligible_invite_targets': sorted(targets), 'can_withdraw': can_withdraw}

    def alliance_action_taken(self, faction):
        """True once `faction` has used its one invite-or-withdraw this turn."""
        return faction in self._alliance_action_taken

    def alliance_members(self, faction):
        """Every faction in `faction`'s alliance, itself included (just {faction}
        when it has none)."""
        return self._alliance_members(faction)

    def _new_alliance_tag(self):
        tag = f'ALLIANCE_{self.game_state._next_alliance_id}'
        self.game_state._next_alliance_id += 1
        return tag

    def _effective_max_alliance_size(self):
        """game_start_settings.max_alliance_size is a ceiling chosen at
        setup, but it can never actually allow an alliance of every
        remaining active faction -- that would mean the alliance forming
        IS the game ending (would_game_end()'s all-active-mutually-allied
        condition), which invite_to_alliance must never trigger as a side
        effect of a normal invite. So the size actually enforced is
        min(max_alliance_size, len(active_factions()) - 1), recomputed
        fresh from the CURRENT active-faction count every time this is
        called -- not just once at setup. As factions are eliminated over
        the course of the game this shrinks, progressively restricting
        what NEW alliances can form or grow into -- confirmed this
        session. It never dissolves an existing alliance that's already
        larger than the current value; only invite_to_alliance's
        validation below consults this at all."""
        return min(self.game_state.max_alliance_size, len(self.game_state.active_factions()) - 1)

    def _check_invite(self, faction, target, ignore_action_taken=False):
        """Raises ValueError unless `faction` may invite `target` right now (invite_to_alliance's
        own checks). ignore_action_taken: leave out the once-a-turn limit -- "could it ask, were the
        action free?", which a bot needs to know before it plans to."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.DIPLOMACY:
            raise ValueError('invite_to_alliance is only valid during the Diplomacy phase')
        if faction in self._alliance_action_taken and not ignore_action_taken:
            raise ValueError(f'{faction} has already taken its one alliance action this turn')
        if target == faction:
            raise ValueError('a faction cannot invite itself')
        if target not in self.game_state.active_factions():
            raise ValueError(f'{target} is not an active faction and cannot be invited')
        if self.game_state.factions[target].alliance is not None:
            raise ValueError(f'{target} is already in an alliance -- it must withdraw first')

        prospective_members = self._alliance_members(faction) | {target}
        effective_max = self._effective_max_alliance_size()
        if len(prospective_members) > effective_max:
            raise ValueError(
                f'accepting would make an alliance of {len(prospective_members)}, '
                f'exceeding the effective max_alliance_size ({effective_max}, '
                f'capped by {len(self.game_state.active_factions())} currently active factions)'
            )
        if not self.game_state.can_rejoin_alliances:
            banned = self.game_state.factions[target].former_allies & prospective_members
            if banned:
                raise ValueError(
                    f"{target} can no longer ally with {sorted(banned)} (can_rejoin_alliances is False)"
                )


    def can_invite_to_alliance(self, faction, target, ignore_action_taken=True):
        """Whether invite_to_alliance(faction, target, ...) would be legal (pure query)."""
        try:
            self._check_invite(faction, target, ignore_action_taken)
        except ValueError:
            return False
        return True

    def invite_to_alliance(self, faction, target, target_accepts):
        """Diplomacy phase, one of the two things `faction` may
        optionally do this turn (the other is withdraw_from_alliance;
        never both -- see _alliance_action_taken). `target` immediately
        decides (alliances.invite_immediate_decision) -- `target_accepts`
        is supplied by the caller, exactly like every other order this
        engine takes a decision as an input rather than making one
        itself; whichever bot/human logic controls `target` is
        responsible for it, consulted synchronously before this call.

        Raises ValueError if the invite could never be validly accepted
        at all -- wrong phase, `faction` already took its one alliance
        action this turn, `target` is `faction` itself or not a valid
        ally-eligible active faction, `target` is already in an alliance
        (must withdraw first, on its own separate turn), accepting would
        exceed _effective_max_alliance_size() (game_start_settings.
        max_alliance_size, further capped by the CURRENT number of
        active factions -- see that method), or -- when
        can_rejoin_alliances is False -- `target` has a former_allies
        conflict with anyone already in `faction`'s alliance. A
        `target_accepts=False` decline is NOT an error (nothing changes,
        but the one-action-per-turn slot is still spent); returns
        `target_accepts` either way."""
        self._check_invite(faction, target)

        self._alliance_action_taken.add(faction)
        asker = self.game_state.factions[faction]
        asker.invites_sent += 1
        asker.last_invited[target] = asker.invites_sent
        if not target_accepts:
            asker.invites_declined[target] = asker.invites_declined.get(target, 0) + 1
            if self.turn_log is not None:
                self.turn_log.record_alliance_declined(self.game_state.global_turn, faction, target)
            return False

        existing_tag = self.game_state.factions[faction].alliance
        tag = existing_tag or self._new_alliance_tag()
        self.game_state.factions[faction].alliance = tag
        self.game_state.factions[target].alliance = tag
        if self.stats is not None:
            self.stats.record_alliance_joined(
                self.game_state.global_turn, faction, target, tag, new_alliance=(existing_tag is None),
            )
        if self.turn_log is not None:
            self.turn_log.record_alliance_joined(
                self.game_state.global_turn, faction, target, tag, new_alliance=(existing_tag is None),
            )
        return True

    def _faction_has_units_on_an_allied_sc(self, faction):
        """True if `faction` currently has at least one unit physically
        present on a Strategic Center owned by one of its OWN allies
        (not itself) -- the design doc's SC lock on withdrawal."""
        terrs = self.data.territories()
        for tid, t in self.game_state.territories.items():
            if terrs[tid]['type'] != 'land' or not self.game_state.is_strategic_center(tid, terrs[tid]):
                continue
            if t.owner is None or t.owner == faction:
                continue
            if not _is_ally_or_self(self.game_state, faction, t.owner):
                continue
            if any(u.owner == faction for u in t.units):
                return True
        return False

    def withdraw_from_alliance(self, faction):
        """Diplomacy phase, the other of the two things `faction` may
        optionally do this turn (see invite_to_alliance) -- there is no
        separate 'last chance' version of this at game-end time; this
        IS the only withdrawal action, and it's this faction's one
        regular turn choice, same as any other turn. Leaves the CURRENT
        alliance entirely -- no longer allied with any of its former
        members (rule 4) -- records former_allies symmetrically for
        game_start_settings.can_rejoin_alliances, and, for each land
        territory a former ally owns that `faction` is still physically
        occupying, marks it contested and queues combat.
        first_round_bonuses' former-ally-reclaim bonus for that ally.
        That bonus -- and the territory itself -- now stays live for the
        betrayed ally across repeated attempts (combat.
        true_territory_loss's betrayal exception, confirmed this
        session): `faction`, the betrayer, can't just claim it by
        default the moment the original owner's first counter-attack
        fails; it's only actually settled once `faction`'s own Capture
        Territory phase finds the original owner with zero presence left
        there (or the original owner reclaims it outright first, on its
        own Capture Territory phase).

        Raises ValueError if the phase/turn/action-slot guards fail, if
        game_start_settings.can_withdraw_from_alliances is False,
        `faction` has no alliance to leave, or `faction` currently has a
        unit on an ally's Strategic Center (the design doc's SC lock)."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.DIPLOMACY:
            raise ValueError('withdraw_from_alliance is only valid during the Diplomacy phase')
        if faction in self._alliance_action_taken:
            raise ValueError(f'{faction} has already taken its one alliance action this turn')
        if not self.game_state.can_withdraw_from_alliances:
            raise ValueError('withdrawing from alliances is disabled (can_withdraw_from_alliances is False)')
        fstate = self.game_state.factions[faction]
        if fstate.alliance is None:
            raise ValueError(f'{faction} is not currently in an alliance')
        if self._faction_has_units_on_an_allied_sc(faction):
            raise ValueError(f"{faction} cannot withdraw while it has units on an ally's Strategic Center")

        self._alliance_action_taken.add(faction)
        former_tag = fstate.alliance
        former_members = self._alliance_members(faction) - {faction}
        for other in former_members:
            fstate.former_allies.add(other)
            self.game_state.factions[other].former_allies.add(faction)
        fstate.alliance = None
        if len(former_members) == 1:
            # An alliance of one is no alliance: the last remaining member is free again
            # (it can be invited, and can invite), rather than stuck under a one-member tag.
            (last,) = former_members
            self.game_state.factions[last].alliance = None
        if self.stats is not None:
            self.stats.record_alliance_withdrawal(self.game_state.global_turn, faction, former_tag, former_members)
        if self.turn_log is not None:
            self.turn_log.record_alliance_withdrawal(self.game_state.global_turn, faction, former_tag, former_members)

        terrs = self.data.territories()
        for tid, t in self.game_state.territories.items():
            if terrs[tid]['type'] != 'land' or t.owner not in former_members:
                continue
            if any(u.owner == faction for u in t.units):
                t.contested_by = (t.contested_by or set()) | {faction, t.owner}
                t.reclaim_bonus_for = t.owner

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

    def process_game_end_check(self, faction):
        """victory.game_end_rule, checked once at the very end of
        `faction`'s full turn -- after the Diplomacy phase, the last of
        the 7 turn_order phases. Pure query+set: sets GameState.game_over
        to match would_game_end() and returns it. NOT a separate
        decision point -- whatever alliance action `faction` took this
        turn (invite_to_alliance, withdraw_from_alliance, or neither) is
        its one regular Alliances-phase choice, already applied earlier
        in this same phase; there is no additional 'last chance' window
        here to avoid the game ending. A driver wanting to keep the game
        going must have `faction` call withdraw_from_alliance itself,
        during its regular turn, before this runs."""
        if faction not in self.game_state.active_factions():
            raise ValueError(f'{faction} is not an active faction')
        if self.game_state.phase != Phase.DIPLOMACY:
            raise ValueError('process_game_end_check is only valid during the Diplomacy phase')

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
        Purchase phase.

        game_start_settings enforcement: if the active faction is
        currently on its own first turn (FactionState.turns_taken == 0)
        and GameState.allow_combat_moves_first_turn /
        allow_noncombat_moves_first_turn is False, this steps STRAIGHT
        PAST Combat Move and/or Non-Combat Move entirely -- GameState.phase
        never becomes that phase at all this turn, so submit_combat_moves/
        submit_noncombat_moves (both gated on the current phase matching)
        already refuse to run without any extra check of their own.
        Combat Resolution is never skipped this way -- it isn't a "move,"
        just auto-resolving whatever's currently contested on the board,
        regardless of whether this faction submitted a fresh combat move
        this turn."""
        idx = _PHASE_ORDER.index(self.game_state.phase)
        idx += 1
        while idx < len(_PHASE_ORDER) - 1 and self._phase_is_skipped_this_turn(_PHASE_ORDER[idx]):
            idx += 1
        if idx < len(_PHASE_ORDER):
            self.game_state.phase = _PHASE_ORDER[idx]

    def _phase_is_skipped_this_turn(self, phase):
        faction = self.game_state.active_faction
        if faction is None or self.game_state.factions[faction].turns_taken > 0:
            return False  # not this faction's first turn -- game_start_settings only ever apply to the first
        if phase == Phase.COMBAT_MOVE:
            return not self.game_state.allow_combat_moves_first_turn
        if phase == Phase.NONCOMBAT_MOVE:
            return not self.game_state.allow_noncombat_moves_first_turn
        return False

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
        once, ever, ACROSS THE WHOLE GAME, not once per turn), increments
        its FactionState.turns_taken (so game_start_settings' first-turn
        combat/non-combat-move skip in advance_phase() only ever applies
        once, to the turn that's now ending), then advances active_faction
        to the next one in active_factions()
        (wrapping around, and naturally skipping anyone eliminated since
        this faction's turn began, since active_factions() is always
        recomputed fresh -- and bumping GameState.round_number exactly
        when it wraps, i.e. once per actual completed lap), increments
        global_turn, and resets phase back to PURCHASE.

        Raises ValueError if the game is already over or this isn't
        called at the end of the Diplomacy phase."""
        if self.game_state.game_over:
            raise ValueError('the game is already over')
        if self.game_state.phase != Phase.DIPLOMACY:
            raise ValueError('advance_turn is only valid at the end of the Diplomacy phase')

        finishing = self.game_state.active_faction
        if finishing is not None:
            for t in self.game_state.territories.values():
                for u in t.units:
                    if u.owner == finishing:
                        u.has_moved_combat = False
                        u.has_moved_noncombat = False
                        u.arrived_amphibiously = False
            self._purchases_confirmed.discard(finishing)
            self._combat_moves_confirmed.discard(finishing)
            self._combat_resolved.discard(finishing)
            self._noncombat_moves_confirmed.discard(finishing)
            self._return_to_base_processed.discard(finishing)
            self._alliance_action_taken.discard(finishing)
            # game_start_settings' first-turn check (FactionState.turns_taken
            # == 0) is only ever true for THIS, its now-concluding turn.
            self.game_state.factions[finishing].turns_taken += 1

        active = self.game_state.active_factions()
        if not active:
            self.game_state.active_faction = None
            self.game_state.game_over = True
            return

        next_index = (active.index(finishing) + 1) % len(active) if finishing in active else 0
        if next_index == 0:
            self.game_state.round_number += 1  # wrapped back to the top of the (possibly-shrunk) turn order: a new lap
        self.game_state.active_faction = active[next_index]
        self.game_state.global_turn += 1
        self.game_state.phase = Phase.PURCHASE
