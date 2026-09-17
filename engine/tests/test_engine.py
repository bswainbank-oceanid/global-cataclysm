import unittest

from engine.engine import GameEngine, PurchaseOrder
from engine.state import GameState, TerritoryState, FactionState, UnitInstance, PowerMode, Phase

UNIT_DEFS = {
    'Infantry': {'category': 'Land', 'cost': 4, 'sc_cost': 3, 'hp': 2, 'purchasable': True,
                 'attack_die': 'D6', 'defense': 5, 'damage': 2, 'combat_move': 1, 'non_combat_move': 2},
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


if __name__ == '__main__':
    unittest.main()
