"""
GameEngine: the phased order-submission API wrapping a GameState,
exposed identically to human and bot callers (see docs/GAME_ARCHITECTURE.md's
build plan, Step 4). Only the Purchase phase is implemented so far --
covering data/rules.json's full `purchase` section (location targeting,
the start-of-turn ownership snapshot, SC-discounted cost, the SC-first-
then-most-remaining-capacity multi-territory allocation with spillover,
naval/land location restrictions, and the contested-land Infantry-only
restriction). Combat Move, Combat Resolution, Non-Combat Move, Capture
Territory, and Deploy + Income are not yet implemented.

Rollback (phase_confirmation): submit_purchases takes the COMPLETE
desired order list every call, wholesale-replacing any previously staged
list for that faction -- there's no separate undo_last()/append() API.
A caller "undoes" a purchase simply by calling submit_purchases again
with a corrected list; nothing is written to GameState (treasury_mpc,
pending_deployment) until confirm_purchases() is called, which is
irreversible for that faction's turn (submit_purchases and
confirm_purchases both then refuse further calls for that faction).
"""
from dataclasses import dataclass

from . import data as _default_data
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
        Returns total_cost in MPC. Raises ValueError on the first
        illegal order; nothing from `orders` is applied to GameState by
        this method regardless -- it's a pure check, used identically by
        submit_purchases (validate only) and confirm_purchases (re-run
        just before placing units, since it's cheap and keeps the
        placement logic from silently drifting out of sync with
        validation)."""
        unit_defs = self.data.units()
        terrs = self.data.territories()
        consumed = {}
        total_cost = 0

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
            if remaining > 0:
                raise ValueError(
                    f'not enough deploy capacity for {order.qty}x {order.unit_type} at {deploy_at} '
                    f'(short by {remaining} across every eligible territory)'
                )

        return total_cost

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

        total_cost = self._resolve_and_cost(orders, faction)
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
        purchase fallbacks, is Deploy + Income's job, not yet
        implemented). Irreversible: submit_purchases and
        confirm_purchases both refuse further calls for this faction
        this turn afterward."""
        if faction not in self.game_state.active_powers():
            raise ValueError(f'{faction} is not an active power')
        if faction in self._purchases_confirmed:
            raise ValueError(f'{faction} has already confirmed purchases this turn')

        orders = self._staged_purchases.get(faction, [])
        total_cost = self._resolve_and_cost(orders, faction)
        unit_defs = self.data.units()

        for order in orders:
            for _ in range(order.qty):
                instance = UnitInstance(
                    unit_id=self.game_state.new_unit_id(),
                    unit_type=order.unit_type,
                    owner=faction,
                    current_hp=unit_defs[order.unit_type]['hp'],
                )
                self.game_state.territories[order.deploy_at].pending_deployment.append(instance)

        self.game_state.factions[faction].treasury_mpc -= total_cost
        self._purchases_confirmed.add(faction)
        self._staged_purchases.pop(faction, None)
