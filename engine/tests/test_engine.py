import random
import unittest

from engine import data as real_data
from engine.combat import BattleResult
from engine.engine import GameEngine, PurchaseOrder, CombatMoveOrder, NonCombatMoveOrder
from engine.state import GameState, TerritoryState, FactionState, UnitInstance, PowerMode, Phase

UNIT_DEFS = {
    'Infantry': {'category': 'Land', 'cost': 4, 'sc_cost': 3, 'hp': 2, 'purchasable': True,
                 'attack_die': 'D6', 'defense': 5, 'damage': 2, 'combat_move': 1, 'non_combat_move': 2},
    'Mechanized Infantry': {'category': 'Land', 'cost': 6, 'sc_cost': 4, 'hp': 3, 'purchasable': True,
                             'attack_die': 'D6', 'defense': 6, 'damage': 3, 'combat_move': 2, 'non_combat_move': 2},
    'Armor': {'category': 'Land', 'cost': 8, 'sc_cost': 6, 'hp': 4, 'purchasable': True,
              'attack_die': 'D8', 'defense': 7, 'damage': 4, 'combat_move': 1, 'non_combat_move': 2},
    'Cruiser': {'category': 'Sea', 'cost': 11, 'sc_cost': 8, 'hp': 5, 'purchasable': True,
                'attack_die': 'D10', 'defense': 7, 'damage': 3, 'combat_move': 2, 'non_combat_move': 2},
    'Fighter': {'category': 'Air', 'cost': 10, 'sc_cost': 7, 'hp': 2, 'purchasable': True,
                'attack_die': 'D8', 'defense': 8, 'damage': 3, 'combat_move': 2, 'non_combat_move': 3},
    'Aircraft Carrier': {'category': 'Sea', 'cost': 14, 'sc_cost': 10, 'hp': 6, 'purchasable': True,
                          'attack_die': None, 'defense': 6, 'damage': None, 'combat_move': 2, 'non_combat_move': 2},
    'Transport': {'category': 'Sea', 'cost': None, 'sc_cost': None, 'hp': 1, 'purchasable': False,
                  'attack_die': None, 'defense': 6, 'damage': None, 'combat_move': '+1*', 'non_combat_move': '+1*'},
}


class FakeData:
    """A hand-built, fully controlled territory graph -- the real
    149-territory map isn't practical for hand-verifying an exact
    multi-territory spillover allocation."""
    def __init__(self, territories, adjacency, unit_defs=None):
        self._territories = territories  # {id: {'type', 'value'?, 'strategic_center'?}}
        self._adjacency = adjacency
        self._units = unit_defs or UNIT_DEFS

    def units(self):
        return self._units

    def territories(self):
        return self._territories

    def rules(self):
        # combat.resolve_battle needs the real ruleset (resolution
        # order, target-selection weighting, promotion thresholds,
        # air-superiority trigger) -- only territories/adjacency/units
        # are faked for movement/purchase test control, not the combat
        # rules themselves.
        return real_data.rules()

    def adjacency(self):
        return self._adjacency


def make_state(data, territory_owners, faction_modes, treasury=None, contested=None,
                units_by_territory=None, pending_by_territory=None, phase=Phase.PURCHASE, global_turn=0):
    gs = GameState(phase=phase, global_turn=global_turn)
    for code, mode in faction_modes.items():
        gs.factions[code] = FactionState(code=code, mode=mode, treasury_mpc=(treasury or {}).get(code, 1000))
    for tid in data.territories():
        gs.territories[tid] = TerritoryState(
            territory_id=tid, owner=territory_owners.get(tid),
            contested_by=(contested or {}).get(tid),
            units=(units_by_territory or {}).get(tid, []),
            pending_deployment=(pending_by_territory or {}).get(tid, []),
        )
    return gs


_next_uid = [1000]


def make_unit(unit_type, owner, purchased_at=None, hp=None):
    _next_uid[0] += 1
    hp = UNIT_DEFS[unit_type]['hp'] if hp is None else hp
    return UnitInstance(unit_id=_next_uid[0], unit_type=unit_type, owner=owner, current_hp=hp, purchased_at=purchased_at)


class TestLandPurchase(unittest.TestCase):
    def test_buy_within_capacity_and_confirm(self):
        # territory 1: land, value 2, owned by NAA -- cap 2.
        data = FakeData(territories={1: {'type': 'land', 'value': 2}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        self.assertEqual(cost, 8)  # 2 x cost 4
        engine.confirm_purchases('NAA')
        self.assertEqual(len(gs.territories[1].pending_deployment), 2)
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 1000 - 8)

    def test_strategic_center_uses_sc_cost(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        self.assertEqual(cost, 6)  # 2 x sc_cost 3

    def test_exceeding_single_territory_capacity_is_rejected(self):
        # cap = value 2 + SC bonus 0 = 2; ordering 3 units should fail.
        data = FakeData(territories={1: {'type': 'land', 'value': 2}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 3, 1)])

    def test_sea_category_unit_cannot_deploy_on_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 1, 1)])

    def test_unowned_land_target_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])

    def test_insufficient_treasury_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, treasury={'NAA': 3})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # costs 4, only 3 available


class TestContestedLandDeployRestriction(unittest.TestCase):
    def test_infantry_allowed_into_contested_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # should not raise

    def test_non_infantry_rejected_from_contested_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Armor', 1, 1)])


class TestSeaDeployAllocation(unittest.TestCase):
    def test_single_adjacent_territory_supplies_cost_and_cap(self):
        # 1 (land, value 3, SC) -- 2 (sea)
        data = FakeData(
            territories={1: {'type': 'land', 'value': 3, 'strategic_center': True}, 2: {'type': 'sea'}},
            adjacency={2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 2, 2)])
        self.assertEqual(cost, 16)  # 2 x sc_cost 8 (SC's cap is 3+2=5, plenty of room)

    def test_no_adjacent_owned_territory_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 1, 2)])

    def test_sc_drawn_first_regardless_of_raw_value(self):
        # 3 (sea) adjacent to 1 (land, value 5, no SC -- cap 5) and
        # 2 (land, value 1, SC -- cap 3). SC goes first even though its
        # raw value/cap is smaller.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        # Buy exactly 3 (the SC's full capacity) -- if the SC goes
        # first, all 3 should be sc_cost (3 x 3 = 9); if the larger
        # territory went first instead, they'd be full cost (3 x 4 = 12).
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 3, 3)])
        self.assertEqual(cost, 9)

    def test_spillover_across_multiple_territories_within_one_order(self):
        # Same setup as above: SC (territory 2, cap 3) then territory 1
        # (cap 5). Buying 5 Infantry should draw 3 from the SC (sc_cost
        # 3 each = 9) and the remaining 2 from territory 1 (cost 4 each
        # = 8), spilling over automatically within a single order.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 5, 3)])
        self.assertEqual(cost, 9 + 8)

    def test_spillover_exhausted_across_all_sources_is_rejected(self):
        # SC cap 3 + territory 1 cap 5 = 8 total; asking for 9 must fail.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 9, 3)])

    def test_two_orders_in_the_same_list_compete_for_the_same_capacity(self):
        # A single land territory (cap 5) backing a sea zone. Two
        # separate orders in the SAME submit_purchases call, totaling 6
        # units, should fail even though each order alone would fit --
        # capacity tracking is shared across the whole submitted list.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}},
            adjacency={2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 3, 2), PurchaseOrder('Armor', 3, 2)])

    def test_land_unit_purchased_at_sea_is_still_just_a_normal_unit_instance(self):
        # No special "Transport" bookkeeping at purchase time -- it's
        # purely a placement detail (movement.py's water-crossing model
        # already treats any land unit sitting in a sea zone as riding
        # one); confirm_purchases just places the ordered unit type.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 2)])
        engine.confirm_purchases('NAA')
        placed = gs.territories[2].pending_deployment
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].unit_type, 'Infantry')


class TestRollbackAndConfirmation(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # "undo" down to 1
        engine.confirm_purchases('NAA')
        self.assertEqual(len(gs.territories[1].pending_deployment), 1)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])
        engine.confirm_purchases('NAA')
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])
        with self.assertRaises(ValueError):
            engine.confirm_purchases('NAA')

    def test_confirm_with_no_prior_submission_is_a_harmless_no_op(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.confirm_purchases('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 1000)

    def test_defensive_faction_cannot_submit_purchases(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.DEFENSIVE})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN})
        gs.phase = Phase.COMBAT_MOVE
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])


class TestDeployPending(unittest.TestCase):
    def test_land_deploy_to_still_owned_territory(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1)
        self.assertEqual(len(gs.territories[1].pending_deployment), 0)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.PURCHASE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.deploy_and_collect_income('NAA')


class TestContestedPurchaseLostFallback(unittest.TestCase):
    def test_falls_back_to_adjacent_controlled_territory(self):
        # territory 1 (pending Infantry, now owned by AAC -- lost this
        # turn) -- territory 2 (still owned by NAA) -- territory 3 (sea).
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 1)
        self.assertEqual(len(gs.territories[1].units), 0)

    def test_falls_back_to_adjacent_sea_when_no_controlled_land(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[3].units), 1)

    def test_lost_outright_when_no_adjacent_controlled_or_sea(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 0)
        self.assertEqual(len(gs.territories[1].units), 0)

    def test_still_owned_territory_is_unaffected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            contested={1: {'NAA', 'AAC'}},  # still contested but NOT lost
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1, 'still-owned (even if contested) territory needs no fallback')


class TestCarrierlessAirDeployFallback(unittest.TestCase):
    def test_redirects_to_purchasing_land_when_no_carrier_anywhere(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1)
        self.assertEqual(len(gs.territories[2].units), 0)

    def test_stays_at_sea_when_own_carrier_already_present(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Aircraft Carrier', 'NAA')]},
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 2)  # carrier + fighter

    def test_stays_at_sea_when_own_carrier_arrives_in_the_same_batch(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1), make_unit('Aircraft Carrier', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 2)

    def test_allied_carrier_does_not_count(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Aircraft Carrier', 'UE')]},
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1, "an ally's carrier shouldn't satisfy the carrierless fallback")


class TestHostileSeaDeployCreatesContested(unittest.TestCase):
    def test_deploying_into_enemy_occupied_zone_creates_contested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'AAC')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_deploying_into_allied_occupied_zone_stays_uncontested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'UE')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertIsNone(gs.territories[2].contested_by)

    def test_deploying_into_empty_zone_stays_uncontested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertIsNone(gs.territories[2].contested_by)


class TestIncomeCollection(unittest.TestCase):
    def test_income_added_to_treasury(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 2, 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, treasury={'NAA': 10}, phase=Phase.DEPLOY_INCOME)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 10 + 3 + (2 + 2))

    def test_contested_territory_contributes_no_income(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, treasury={'NAA': 10}, phase=Phase.DEPLOY_INCOME,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 10)


class TestGlobalRecoverySweep(unittest.TestCase):
    def test_unit_heals_after_a_full_round_has_elapsed(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=2,
        )
        # 2 active powers -- damaged on global_turn 0, so a full round
        # (2 turns) has elapsed by global_turn 2.
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(damaged.current_hp, UNIT_DEFS['Armor']['hp'])

    def test_unit_does_not_heal_before_a_full_round_has_elapsed(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=1,
        )
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(damaged.current_hp, 1, 'only 1 turn elapsed, not a full round (2 active powers)')

    def test_recovery_sweeps_the_whole_board_not_just_the_active_faction(self):
        # The unit healed above belongs to AAC, not the acting faction
        # NAA -- confirming the sweep really is global.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=2,
        )
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertGreater(damaged.current_hp, 1)


class TestCombatMoveExecution(unittest.TestCase):
    def test_single_hop_attack_marks_contested_and_relocates_unit(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [attacker], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(attacker.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertNotIn(attacker, gs.territories[1].units)
        self.assertIn(attacker, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})
        self.assertTrue(attacker.has_moved_combat)
        self.assertEqual(gs.territories[2].owner, 'AAC', 'still contested -- not captured until the battle resolves')

    def test_single_hop_capture_of_empty_territory_flips_ownership(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Armor', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'NAA')
        self.assertIsNone(gs.territories[2].contested_by, 'an uncontested capture never marks the territory contested')

    def test_mech_inf_blitz_captures_en_route_and_attacks(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        mover = make_unit('Mechanized Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover], 3: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2, 3])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'NAA', 'captured on the way through, even though the unit kept moving')
        self.assertIn(mover, gs.territories[3].units)
        self.assertEqual(gs.territories[3].contested_by, {'NAA', 'AAC'})

    def test_air_combat_move_never_captures_only_marks_contested(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},  # territory 2 is EMPTY -- air alone can't attack nothing, so...
        )
        engine = GameEngine(gs, data)
        # ...legal_air_move_destinations correctly rejects this (no attack target).
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])

    def test_air_combat_move_attacks_occupied_territory(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})
        self.assertEqual(gs.territories[2].owner, 'AAC', "air alone can't capture")

    def test_unit_not_found_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(99999, [1, 2])])

    def test_wrong_owner_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        other_faction_unit = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [other_faction_unit]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(other_faction_unit.unit_id, [1, 2])])

    def test_path_origin_mismatch_is_rejected(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 3: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        # mover is actually at 1, not 2
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [2, 3])])

    def test_cannot_move_the_same_unit_twice_in_one_submission(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [
                CombatMoveOrder(mover.unit_id, [1, 2]),
                CombatMoveOrder(mover.unit_id, [1, 3]),
            ])

    def test_later_order_can_depend_on_an_earlier_orders_capture(self):
        # Unit A captures territory 2 (empty foreign land). Unit B,
        # starting at territory 1 too, then stages THROUGH territory 2
        # (now NAA's) to attack territory 3 -- only legal because unit
        # A's capture already applied within this same submission.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        unit_a = make_unit('Infantry', 'NAA')
        unit_b = make_unit('Mechanized Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [unit_a, unit_b], 3: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [
            CombatMoveOrder(unit_a.unit_id, [1, 2]),
            CombatMoveOrder(unit_b.unit_id, [1, 2, 3]),
        ])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'NAA')
        self.assertIn(unit_a, gs.territories[2].units)
        self.assertIn(unit_b, gs.territories[3].units)
        self.assertEqual(gs.territories[3].contested_by, {'NAA', 'AAC'})


class TestCombatMoveRollback(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.submit_combat_moves('NAA', [])  # "undo" -- don't move it after all
        engine.confirm_combat_moves('NAA')
        self.assertIn(mover, gs.territories[1].units)
        self.assertFalse(mover.has_moved_combat)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [])
        with self.assertRaises(ValueError):
            engine.confirm_combat_moves('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.PURCHASE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(1, [1, 2])])

    def test_defensive_faction_cannot_submit_combat_moves(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.DEFENSIVE, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(1, [1, 2])])


class ScriptedRNG:
    """Test double for combat resolution: randint() pops scripted rolls
    in order; choices()/choice() always pick the first candidate,
    ignoring weights -- deterministic outcomes without needing to
    re-verify combat.py's own hit/damage math (already covered in
    test_combat.py)."""
    def __init__(self, rolls):
        self.rolls = list(rolls)

    def randint(self, a, b):
        return self.rolls.pop(0)

    def choices(self, population, weights=None, k=1):
        return [population[0]]

    def choice(self, population):
        return population[0]


class TestDeclaredBattlesAndGathering(unittest.TestCase):
    def test_declared_battles_returns_sea_before_land(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'sea'}}, adjacency={},
        )
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}, 3: {'NAA', 'AAC'}},
            units_by_territory={1: [make_unit('Infantry', 'NAA')], 3: [make_unit('Cruiser', 'NAA')]},
        )
        engine = GameEngine(gs, data)
        battles = engine.declared_battles('NAA')
        self.assertEqual(battles, [(3, 'sea'), (1, 'land')])

    def test_excludes_uncontested_territories(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={1: [make_unit('Infantry', 'NAA')]},
        )
        engine = GameEngine(gs, data)
        self.assertEqual(engine.declared_battles('NAA'), [])

    def test_excludes_territories_without_the_factions_own_units(self):
        # Contested, but by two OTHER factions entirely -- NAA has no
        # units there, so it's not NAA's battle to fight this turn.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'UE', 'AAC'}},
            units_by_territory={1: [make_unit('Infantry', 'AAC')]},
        )
        engine = GameEngine(gs, data)
        self.assertEqual(engine.declared_battles('NAA'), [])

    def test_gather_battle_units_pools_multiple_non_allied_defending_factions(self):
        attacker = make_unit('Infantry', 'NAA')
        defender_aac = make_unit('Infantry', 'AAC')
        defender_ue = make_unit('Armor', 'UE')
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'NAA', 'AAC', 'UE'}},
            units_by_territory={1: [attacker, defender_aac, defender_ue]},
        )
        engine = GameEngine(gs, data)
        attacker_units, defender_units = engine.gather_battle_units(1, 'NAA')
        self.assertEqual(attacker_units, [attacker])
        self.assertEqual({u.unit_id for u in defender_units}, {defender_aac.unit_id, defender_ue.unit_id})

    def test_gather_battle_units_excludes_allied_units_from_defender_pool(self):
        attacker = make_unit('Infantry', 'NAA')
        ally_unit = make_unit('Infantry', 'UE')
        enemy_unit = make_unit('Armor', 'AAC')
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'NAA', 'AAC', 'UE'}},
            units_by_territory={1: [attacker, ally_unit, enemy_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        attacker_units, defender_units = engine.gather_battle_units(1, 'NAA')
        self.assertEqual(attacker_units, [attacker])
        self.assertEqual(defender_units, [enemy_unit])


class TestResolveCombatEndToEnd(unittest.TestCase):
    def test_attacker_wins_removes_defender_and_clears_contested(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Infantry: D6, defense 5, damage 2, hp 2 -- attacker's roll of 6
        # cleanly kills the hp-2 defender in one hit; defender's roll of
        # 1 misses back.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([6, 1]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, 'defender_eliminated')
        self.assertIn(attacker, gs.territories[1].units)
        self.assertNotIn(defender, gs.territories[1].units)
        self.assertIsNone(gs.territories[1].contested_by)

    def test_defender_wins_removes_attacker_and_clears_contested(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([1, 6]))
        self.assertEqual(results[0].outcome, 'attacker_eliminated')
        self.assertNotIn(attacker, gs.territories[1].units)
        self.assertIn(defender, gs.territories[1].units)
        self.assertIsNone(gs.territories[1].contested_by)

    def test_contested_outcome_keeps_contested_by_set(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Armor', 'NAA')
        defender = make_unit('Armor', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Armor: D8, defense 7 -- a roll of 3 misses cleanly every round
        # for both sides (below defense, not the die max) -- 3 rounds x
        # 2 rolls = 6 scripted misses, nobody dies.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([3, 3, 3, 3, 3, 3]))
        self.assertEqual(results[0].outcome, 'contested')
        self.assertIn(attacker, gs.territories[1].units)
        self.assertIn(defender, gs.territories[1].units)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')

    def test_already_resolved_this_turn_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION)
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA')
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.NEUTRAL}, phase=Phase.COMBAT_RESOLUTION)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')


class TestEmergencyLandingConsequence(unittest.TestCase):
    """Exercises _apply_battle_outcome/_resolve_stranded_defender_aircraft
    directly against a hand-built BattleResult -- keeps this focused on
    the engine-level consequence logic without re-driving combat.py's
    own dice math (covered in test_combat.py)."""

    def test_surviving_defender_aircraft_relocated_when_own_carrier_destroyed(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        carrier = make_unit('Aircraft Carrier', 'AAC')
        fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [fighter]},  # carrier already removed by _apply_battle_outcome's dead-unit cleanup
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[fighter.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[carrier.unit_id],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertNotIn(fighter, gs.territories[2].units)
        self.assertIn(fighter, gs.territories[1].units, "AAC's own land is the only qualifying emergency landing spot")

    def test_surviving_defender_aircraft_stays_when_own_carrier_survives(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        carrier = make_unit('Aircraft Carrier', 'AAC')
        fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [carrier, fighter]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[carrier.unit_id, fighter.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertIn(fighter, gs.territories[2].units, "the carrier survived -- no emergency landing needed")

    def test_attacker_aircraft_unaffected_by_emergency_landing(self):
        # Attacker's own carrier destroyed -- defender-only mechanic, so
        # the attacker's surviving Fighter should be left exactly where it is.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        attacker_carrier = make_unit('Aircraft Carrier', 'NAA')
        attacker_fighter = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [attacker_fighter]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[attacker_fighter.unit_id], surviving_defender_ids=[],
            eliminated_attacker_ids=[attacker_carrier.unit_id], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertIn(attacker_fighter, gs.territories[2].units)

    def test_land_battle_never_triggers_emergency_landing_check(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        survivor = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={1: [survivor]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[survivor.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(1, 'land', 'NAA', result, random.Random(1))
        self.assertIn(survivor, gs.territories[1].units)


class TestNonCombatMoveExecution(unittest.TestCase):
    def test_simple_move_to_own_territory(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(mover, gs.territories[1].units)
        self.assertIn(mover, gs.territories[2].units)
        self.assertTrue(mover.has_moved_noncombat)

    def test_entering_a_territory_the_mover_is_already_contesting_is_legal(self):
        # NAA is already fighting for territory 2 (its own combat-moved
        # unit is there) -- reinforcing via a non-combat move needs no
        # fresh attack declaration.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        reinforcement = make_unit('Infantry', 'NAA')
        already_there = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            contested={2: {'NAA', 'AAC'}},
            units_by_territory={1: [reinforcement], 2: [already_there, defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(reinforcement.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(reinforcement, gs.territories[2].units)

    def test_entering_a_territory_contested_by_others_only_is_also_legal(self):
        # Contested between two OTHER factions entirely -- NAA isn't
        # part of it yet, but can still walk in via non-combat move.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN},
            phase=Phase.NONCOMBAT_MOVE, contested={2: {'AAC', 'UE'}}, units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)

    def test_cannot_noncombat_move_after_already_combat_moving(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])

    def test_air_can_noncombat_move_after_already_combat_moving(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)

    def test_illegal_destination_is_rejected(self):
        # Clean (uncontested), non-allied foreign land -- never a legal
        # non-combat move target.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])

    def test_cannot_move_the_same_unit_twice(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA', 3: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [
                NonCombatMoveOrder(mover.unit_id, 2),
                NonCombatMoveOrder(mover.unit_id, 3),
            ])


class TestNonCombatMoveRollback(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.submit_noncombat_moves('NAA', [])  # "undo"
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[1].units)
        self.assertFalse(mover.has_moved_noncombat)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [])
        with self.assertRaises(ValueError):
            engine.confirm_noncombat_moves('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(1, 2)])

    def test_defensive_faction_cannot_submit_noncombat_moves(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.DEFENSIVE}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(1, 2)])


if __name__ == '__main__':
    unittest.main()
