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

    def test_single_hop_entry_into_empty_territory_marks_it_contested_not_owned(self):
        # Confirmed this session: even an entirely undefended entry
        # never captures outright -- it's marked contested, and actual
        # ownership is resolved later, in Capture Territory.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Armor', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'AAC', 'not captured immediately -- Capture Territory resolves it later')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_mech_inf_blitz_marks_every_entered_territory_contested(self):
        # Confirmed this session: "even if it's just a Mech Inf running
        # through" -- every territory entered or passed through is
        # marked contested, not just the final, defended stop.
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
        self.assertEqual(gs.territories[2].owner, 'AAC', 'passed through, not captured immediately')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'}, 'marked contested even though the unit kept moving')
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

    def test_a_later_order_sees_contested_state_an_earlier_order_just_created(self):
        # Unit A attacks territory 2 first in this submission, marking
        # it contested. Unit B, starting elsewhere, then joins that same
        # fight -- demonstrating _execute_combat_moves threads each
        # order's mutation into the game_state the NEXT order sees,
        # rather than validating every order against a frozen snapshot
        # of the turn's starting board (ownership no longer flips
        # immediately on an empty capture, so this is no longer about
        # "enabling" a move that would otherwise be illegal -- both
        # orders would succeed independently too -- but the resulting
        # contested_by must still correctly reflect BOTH units' factions
        # merged from two separate calls, not just the second one's).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2], 4: [2]},
        )
        unit_a = make_unit('Infantry', 'NAA')
        unit_b = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 4: 'NAA'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [unit_a], 2: [defender], 4: [unit_b]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [
            CombatMoveOrder(unit_a.unit_id, [1, 2]),
            CombatMoveOrder(unit_b.unit_id, [4, 2]),
        ])
        engine.confirm_combat_moves('NAA')
        self.assertIn(unit_a, gs.territories[2].units)
        self.assertIn(unit_b, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'}, 'no duplication or loss across the two sequential calls')


class TestCombatMoveCarrierRideAlong(unittest.TestCase):
    def test_default_ride_along_with_no_order_of_its_own(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(carrier, gs.territories[2].units)
        self.assertIn(rider, gs.territories[2].units, 'a co-located rider with no order of its own flies into the attack too')
        self.assertTrue(rider.has_moved_combat)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_swept_rider_becomes_a_real_combatant_via_gather_battle_units(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        attacker_units, defender_units = engine.gather_battle_units(2, 'NAA')
        self.assertIn(rider, attacker_units, 'presence alone is enough -- gather_battle_units needed no changes')

    def test_rider_gets_return_to_base_bookkeeping(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(rider.combat_move_origin, 1)
        self.assertEqual(rider.based_on_carrier, carrier.unit_id)

    def test_own_order_excludes_a_rider_regardless_of_submission_order(self):
        # Rider has its OWN attack target, different from the carrier's --
        # tested with the rider's order BOTH before and after the
        # carrier's in the submitted list, since combat move has no
        # valid "chaining" reading (unlike non-combat) and must exclude
        # it either way.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        for rider_order_first in (True, False):
            with self.subTest(rider_order_first=rider_order_first):
                carrier = make_unit('Aircraft Carrier', 'NAA')
                rider = make_unit('Fighter', 'NAA')
                sea_defender = make_unit('Cruiser', 'AAC')
                land_defender = make_unit('Infantry', 'AAC')
                gs = make_state(
                    data, {3: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
                    units_by_territory={1: [carrier, rider], 2: [sea_defender], 3: [land_defender]},
                )
                engine = GameEngine(gs, data)
                rider_order = CombatMoveOrder(rider.unit_id, [1, 3])
                carrier_order = CombatMoveOrder(carrier.unit_id, [1, 2])
                orders = [rider_order, carrier_order] if rider_order_first else [carrier_order, rider_order]
                engine.submit_combat_moves('NAA', orders)
                engine.confirm_combat_moves('NAA')
                self.assertIn(rider, gs.territories[3].units, "the rider's own attack should stick")
                self.assertNotIn(rider, gs.territories[2].units, "not swept along despite being co-located when it started")
                self.assertIn(carrier, gs.territories[2].units)

    def test_only_air_units_are_swept_not_sea_or_land(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        escort = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, escort], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(escort, gs.territories[1].units, 'a co-located Cruiser is not a rider -- ride-along is air-only')

    def test_only_same_faction_riders_are_swept(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        ally_rider = make_unit('Fighter', 'UE')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, ally_rider], 2: [defender]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(ally_rider, gs.territories[1].units, "an ally's aircraft isn't NAA's to sweep along")


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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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
        engine.process_return_to_base('NAA')
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

    def test_submit_noncombat_moves_requires_return_to_base_first(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [])


class TestReturnToBase(unittest.TestCase):
    def test_land_takeoff_returns_to_origin_territory(self):
        # Fighter attacked from territory 1 (still NAA's) and is now
        # sitting at territory 2 (its attack destination) after combat.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertNotIn(flyer, gs.territories[2].units)
        self.assertIn(flyer, gs.territories[1].units)
        self.assertTrue(flyer.has_moved_noncombat)
        self.assertIsNone(flyer.combat_move_origin)

    def test_carrier_takeoff_returns_to_carrier_wherever_it_now_is(self):
        # Flyer departed carrier's original zone (territory 1). The
        # carrier has SINCE moved (its own order, already applied) to
        # territory 3. The flyer should return to territory 3, not 1.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}}, adjacency={},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        flyer.based_on_carrier = carrier.unit_id
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer], 3: [carrier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertIn(flyer, gs.territories[3].units)
        self.assertTrue(flyer.has_moved_noncombat)

    def test_carrier_destroyed_falls_through_to_regular_move(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={2: [1]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        flyer.based_on_carrier = 9999  # no unit anywhere has this id -- destroyed
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertIn(flyer, gs.territories[2].units, 'left exactly where it was -- no auto-relocation possible')
        self.assertFalse(flyer.has_moved_noncombat, 'free to receive a normal non-combat move order instead')
        self.assertIsNone(flyer.combat_move_origin, 'the return-to-base attempt is still consumed either way')

    def test_units_without_a_combat_move_this_turn_are_unaffected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        untouched = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [untouched]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertFalse(untouched.has_moved_noncombat)

    def test_cannot_process_return_to_base_twice(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.NEUTRAL}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')


class TestCarrierRideAlong(unittest.TestCase):
    def test_default_ride_along_with_no_order_of_its_own(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(carrier.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(carrier, gs.territories[2].units)
        self.assertIn(flyer, gs.territories[2].units, 'co-located plane with no order of its own rides along')
        self.assertTrue(flyer.has_moved_noncombat)

    def test_preemption_a_planes_own_earlier_move_excludes_it_from_the_ride(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3], 3: [1]},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {3: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [
            NonCombatMoveOrder(flyer.unit_id, 3),  # plane's own move, processed first -- flies to friendly land
            NonCombatMoveOrder(carrier.unit_id, 2),  # carrier's own move, processed after
        ])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[3].units, "the plane's own move should stick, not get dragged to the carrier's zone")
        self.assertIn(carrier, gs.territories[2].units)

    def test_chaining_a_plane_that_flies_onto_the_carrier_then_rides_its_later_move(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3]},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [flyer], 2: [carrier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [
            NonCombatMoveOrder(flyer.unit_id, 2),  # plane flies onto the carrier's CURRENT zone, using its own move
            NonCombatMoveOrder(carrier.unit_id, 3),  # carrier then moves on -- plane should ride along for free
        ])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[3].units, "chaining: the plane should ride the carrier's later move too")
        self.assertIn(carrier, gs.territories[3].units)

    def test_only_same_faction_air_units_are_swept_along(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        own_flyer = make_unit('Fighter', 'NAA')
        ally_flyer = make_unit('Fighter', 'UE')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, own_flyer, ally_flyer]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(carrier.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(own_flyer, gs.territories[2].units)
        self.assertIn(ally_flyer, gs.territories[1].units, "an ally's aircraft isn't swept along by NAA's carrier order")


class TestStrandedAircraftCheck(unittest.TestCase):
    def test_aircraft_without_own_carrier_is_lost(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(flyer, gs.territories[1].units)

    def test_aircraft_with_own_carrier_survives(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[1].units)

    def test_allied_carrier_does_not_save_a_stranded_aircraft(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        ally_carrier = make_unit('Aircraft Carrier', 'UE')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [ally_carrier, flyer]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(flyer, gs.territories[1].units, "an ally's carrier doesn't count -- only your own")

    def test_land_units_are_never_stranded(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        soldier = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [soldier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(soldier, gs.territories[1].units)


class TestCaptureTerritory(unittest.TestCase):
    def test_claims_unoccupied_territory_ran_through(self):
        # Nobody's land units are physically there -- faction passed
        # through and kept moving -- but it's still owed the claim.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_claims_territory_with_only_own_land_units_remaining(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        survivor = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [survivor]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_leaves_ownership_unchanged_when_enemy_land_units_remain(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'AAC', 'still genuinely contested -- ownership stays put')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_air_only_survivors_do_not_block_the_claim(self):
        # The defender's only survivor is a Fighter -- air can't hold ground.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        surviving_fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [surviving_fighter]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_allied_land_units_do_not_block_the_claim(self):
        # NAA has its OWN land unit here too, so per "if you have one
        # land unit, you can keep it," NAA wins even though its ally is
        # also present -- an ally's presence never BLOCKS faction's own claim.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        own_unit = make_unit('Infantry', 'NAA')
        ally_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [own_unit, ally_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA', "an ally's land presence doesn't block faction's own claim")

    def test_ally_claims_the_territory_when_faction_has_no_land_units_left(self):
        # Confirmed this session: if faction's OWN land units were all
        # eliminated but an ally's weren't, the ALLY claims it -- even
        # though it's still faction's own Capture Territory phase doing
        # the awarding.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ally_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [ally_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'UE', "the ally claims it, not NAA")

    def test_multiple_allies_present_greatest_land_cost_wins(self):
        # UE has one Armor (cost 8); GPC has two Infantry (cost 4 each =
        # 8 total)... make them clearly unequal: UE gets an Armor (8),
        # GPC gets a single Infantry (4) -- UE should win.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ue_unit = make_unit('Armor', 'UE')
        gpc_unit = make_unit('Infantry', 'GPC')
        gs = make_state(
            data, {1: 'AAC'},
            {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN, 'GPC': PowerMode.HUMAN},
            phase=Phase.CAPTURE, contested={1: {'NAA', 'AAC', 'UE', 'GPC'}},
            units_by_territory={1: [ue_unit, gpc_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['GPC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'UE', 'Armor (cost 8) beats a single Infantry (cost 4)')

    def test_multiple_allies_tied_on_cost_turn_order_breaks_the_tie(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ue_unit = make_unit('Infantry', 'UE')
        gpc_unit = make_unit('Infantry', 'GPC')
        gs = make_state(
            data, {1: 'AAC'},
            {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN, 'GPC': PowerMode.HUMAN},
            phase=Phase.CAPTURE, contested={1: {'NAA', 'AAC', 'UE', 'GPC'}},
            units_by_territory={1: [ue_unit, gpc_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['GPC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        # tied on cost (one Infantry each) -- UE was declared to
        # make_state before GPC, so UE comes first in GameState.factions'
        # iteration order and should win the tie.
        self.assertEqual(gs.territories[1].owner, 'UE', 'tied on cost -- earlier turn order wins')

    def test_two_other_powers_still_contesting_leaves_ownership_alone(self):
        # Neither AAC nor UE is allied with NAA (or each other); both
        # still have land units present -- even the nominal owner being
        # a THIRD, unrelated faction (simulating one that's since been
        # eliminated) shouldn't matter -- nothing resolves in NAA's
        # favor while two other powers are still genuinely contesting.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        aac_unit = make_unit('Infantry', 'AAC')
        ue_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'PAF'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [aac_unit, ue_unit]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'PAF', 'original ownership maintained regardless of NAA')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC', 'UE'})

    def test_faction_not_in_contested_by_is_untouched(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN, 'UE': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'AAC', 'UE'}},  # NAA has no stake in this one
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'AAC')
        self.assertEqual(gs.territories[1].contested_by, {'AAC', 'UE'})

    def test_sea_territories_are_never_touched(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')  # should not raise
        self.assertIsNone(gs.territories[1].owner)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'}, 'sea contested status is Combat Resolution\'s concern, not this phase\'s')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_capture_territory('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': PowerMode.NEUTRAL, 'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_capture_territory('NAA')


class TestEliminationCheck(unittest.TestCase):
    def test_faction_with_zero_scs_is_eliminated(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertTrue(gs.factions['AAC'].eliminated)

    def test_faction_with_exactly_one_sc_is_eliminated(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertTrue(gs.factions['AAC'].eliminated)

    def test_faction_with_two_scs_survives(self):
        data = FakeData(
            territories={1: {'type': 'land', 'strategic_center': True}, 2: {'type': 'land', 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(data, {1: 'AAC', 2: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertFalse(gs.factions['AAC'].eliminated)

    def test_eliminated_factions_units_are_removed_from_the_whole_board(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}},
            adjacency={},
        )
        stranded_here = make_unit('Infantry', 'AAC')
        stranded_there = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            units_by_territory={1: [stranded_here], 2: [stranded_there]},
        )
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertNotIn(stranded_here, gs.territories[1].units)
        self.assertNotIn(stranded_there, gs.territories[2].units)

    def test_contested_sc_still_counts_toward_the_registered_owner(self):
        # SC 1 is contested but AAC still owns it (ownership only
        # changes via process_capture_territory, already run this turn)
        # -- AAC's count should still include it, keeping AAC above 1.
        data = FakeData(
            territories={1: {'type': 'land', 'strategic_center': True}, 2: {'type': 'land', 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'AAC'}, {'AAC': PowerMode.HUMAN, 'NAA': PowerMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'AAC', 'NAA'}},
        )
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertFalse(gs.factions['AAC'].eliminated)

    def test_eliminated_faction_is_excluded_from_active_powers(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land', 'strategic_center': True}, 3: {'type': 'land', 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA', 3: 'NAA'}, {'AAC': PowerMode.HUMAN, 'NAA': PowerMode.HUMAN}, phase=Phase.CAPTURE,
        )
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertNotIn('AAC', gs.active_powers())
        self.assertIn('NAA', gs.active_powers())

    def test_neutral_factions_are_never_eliminated(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'PAF'}, {'PAF': PowerMode.NEUTRAL}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertFalse(gs.factions['PAF'].eliminated)

    def test_defensive_faction_can_be_eliminated(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        stranded = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': PowerMode.DEFENSIVE}, phase=Phase.CAPTURE,
            units_by_territory={1: [stranded]},
        )
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        self.assertTrue(gs.factions['AAC'].eliminated)
        self.assertNotIn(stranded, gs.territories[1].units)

    def test_calling_twice_is_idempotent(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        engine.process_elimination_check()
        engine.process_elimination_check()  # should not raise
        self.assertTrue(gs.factions['AAC'].eliminated)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'AAC': PowerMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_elimination_check()


if __name__ == '__main__':
    unittest.main()
