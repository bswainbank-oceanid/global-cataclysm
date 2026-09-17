"""
GameEngine: the phased order-submission API wrapping a GameState,
exposed identically to human and bot callers (see docs/GAME_ARCHITECTURE.md's
build plan, Step 4). Purchase and Deploy + Income are implemented so
far -- Purchase covers data/rules.json's full `purchase` section
(location targeting, the start-of-turn ownership snapshot, SC-discounted
cost, the SC-first-then-most-remaining-capacity multi-territory
allocation with spillover, naval/land location restrictions, and the
contested-land Infantry-only restriction); Deploy + Income covers
placing pending_deployment onto the board (with the carrierless-air and
lost-contested-purchase fallbacks, and hostile-sea-zone deploys becoming
contested), income collection, and the global recovery/heal sweep.
Combat Move, Combat Resolution, Non-Combat Move, and Capture Territory
are not yet implemented -- notably, nothing here yet transitions
GameState.phase itself between phases; callers currently set it
directly (see the tests).

Rollback (phase_confirmation): submit_purchases takes the COMPLETE
desired order list every call, wholesale-replacing any previously staged
list for that faction -- there's no separate undo_last()/append() API.
A caller "undoes" a purchase simply by calling submit_purchases again
with a corrected list; nothing is written to GameState (treasury_mpc,
pending_deployment) until confirm_purchases() is called, which is
irreversible for that faction's turn (submit_purchases and
confirm_purchases both then refuse further calls for that faction).
Deploy + Income has no such staging -- it's automatic and irreversible
by nature (see turn_order's Deploy + Income entry), so it's just one
direct call.
"""
from dataclasses import dataclass

from . import data as _default_data
from .economy import compute_income
from .movement import _is_ally_or_self
from .state import Phase, UnitInstance


@dataclass
class PurchaseOrder:
    unit_type: str
    qty: int
    deploy_at: int  # territory_id, land or sea -- the actual final deploy target


class GameEngine:
    def __init__(self, game_state, data_module=None):
        self.game_state = game_state
        self.data = data_module or _default_data
        self._staged_purchases = {}  # faction_code -> [PurchaseOrder, ...]
        self._purchases_confirmed = set()

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
        if faction not in self.game_state.active_powers():
            raise ValueError(f'{faction} is not an active power and cannot submit purchases')
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
        if faction not in self.game_state.active_powers():
            raise ValueError(f'{faction} is not an active power')
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
        if faction not in self.game_state.active_powers():
            raise ValueError(f'{faction} is not an active power')
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
            return
        # Lost during the turn (only ever Infantry -- the only unit
        # type contested_land_deploy_restriction lets into a contested
        # territory in the first place) -- purchase.
        # contested_purchase_lost_during_turn_fallback's chain.
        fallback = self._find_fallback_for_lost_purchase(tid, faction)
        if fallback is not None:
            self.game_state.territories[fallback].units.extend(pending)
        # else: no adjacent controlled territory or sea zone -- lost outright, never placed.

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
        current_global_turn - last_combat_global_turn >= num_powers,
        where num_powers is len(active_powers()) (turn_order_note)."""
        unit_defs = self.data.units()
        num_powers = len(self.game_state.active_powers())
        if num_powers == 0:
            return
        for t in self.game_state.territories.values():
            for u in t.units:
                if u.last_combat_global_turn is None:
                    continue
                if self.game_state.global_turn - u.last_combat_global_turn >= num_powers:
                    u.current_hp = u.effective_stats(unit_defs)['max_hp']
