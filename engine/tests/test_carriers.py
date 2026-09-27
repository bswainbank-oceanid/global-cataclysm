"""Aircraft carriers hold at most their capacity (3) of their owner's aircraft; aircraft may land to
wait for a carrier being deployed this turn; an aircraft whose base is lost gets an open non-combat
move; and every aircraft left with nowhere to land crashes at the end of Non-Combat Move -- logged."""
import unittest

from engine.engine import CombatMoveOrder, GameEngine, NonCombatMoveOrder, PurchaseOrder
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, ScriptedRNG, make_state, make_unit
from engine.turn_log import TurnLog

# 1 NAA land -- 2 sea -- 3 sea -- 4 AAC land, and 5 more sea beside 2
MAP = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}, 3: {'type': 'sea'},
                            4: {'type': 'land', 'value': 1}, 5: {'type': 'sea'}},
               adjacency={1: [2], 2: [1, 3, 5], 3: [2, 4], 4: [3], 5: [2]})


def game(units, phase=Phase.NONCOMBAT_MOVE, pending=None, modes=None):
    gs = make_state(MAP, {1: 'NAA', 4: 'AAC'}, modes or {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                    units_by_territory=units, pending_by_territory=pending, phase=phase)
    gs.active_faction = 'NAA'
    engine = GameEngine(gs, MAP, turn_log=TurnLog())
    return gs, engine


def fighters(n, owner='NAA'):
    return [make_unit('Fighter', owner) for _ in range(n)]


class TestCapacity(unittest.TestCase):
    def test_no_landing_on_a_full_carrier(self):
        planes = fighters(4)
        gs, engine = game({1: [planes[3]], 2: [make_unit('Aircraft Carrier', 'NAA')] + planes[:3]})
        engine.process_return_to_base('NAA')
        self.assertNotIn(2, engine.legal_noncombat_move_options('NAA').get(planes[3].unit_id, {}).get('destinations', []))
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(planes[3].unit_id, 2)])

    def test_a_moving_carrier_takes_at_most_three(self):
        carrier, planes = make_unit('Aircraft Carrier', 'NAA'), fighters(4)
        gs, engine = game({2: [carrier] + planes})
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(carrier.unit_id, 5)])
        engine.confirm_noncombat_moves('NAA')
        self.assertEqual(sum(1 for u in gs.territories[5].units if u.unit_type == 'Fighter'), 3)

    def test_deployed_aircraft_beyond_the_capacity_go_to_the_land_that_paid(self):
        new = [make_unit('Fighter', 'NAA', purchased_at=1) for _ in range(4)]
        gs, engine = game({}, phase=Phase.DEPLOY_INCOME,
                          pending={2: [make_unit('Aircraft Carrier', 'NAA', purchased_at=1)] + new})
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(sum(1 for u in gs.territories[2].units if u.unit_type == 'Fighter'), 3)
        self.assertEqual(sum(1 for u in gs.territories[1].units if u.unit_type == 'Fighter'), 1)


class TestLandingForACarrierBeingDeployed(unittest.TestCase):
    def test_up_to_three_aircraft_land_to_wait_for_it_and_survive(self):
        planes = fighters(4)
        gs, engine = game({1: planes}, phase=Phase.PURCHASE)
        engine.submit_purchases('NAA', [PurchaseOrder('Aircraft Carrier', 1, 2)])
        engine.confirm_purchases('NAA')
        gs.phase = Phase.NONCOMBAT_MOVE
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(p.unit_id, 2) for p in planes[:3]])
        opts = engine.move_options_with_staged('NAA', 'noncombat')
        self.assertNotIn(2, opts.get(planes[3].unit_id, {}).get('destinations', []), 'the fourth has no place')
        engine.confirm_noncombat_moves('NAA')
        self.assertEqual(sum(1 for u in gs.territories[2].units if u.unit_type == 'Fighter'), 3,
                         'aircraft waiting for a carrier being deployed do not crash')
        self.assertFalse([e for e in engine.turn_log.events if e['kind'] == 'aircraft_lost'])
        gs.phase = Phase.DEPLOY_INCOME
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(sorted(u.unit_type for u in gs.territories[2].units), ['Aircraft Carrier'] + ['Fighter'] * 3)


class TestLostBase(unittest.TestCase):
    def test_an_aircraft_whose_carrier_sank_gets_an_open_non_combat_move(self):
        carrier, plane = make_unit('Aircraft Carrier', 'NAA'), make_unit('Fighter', 'NAA')
        foe = make_unit('Submarine', 'AAC')
        gs, engine = game({2: [carrier, plane], 3: [foe]}, phase=Phase.COMBAT_MOVE)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [2, 3])])  # the fighter rides along
        engine.confirm_combat_moves('NAA')
        gs.phase = Phase.COMBAT_RESOLUTION
        carrier.current_hp = 1
        # the fighter hits nothing (the sub can't be seen by aircraft); the carrier misses; the sub sinks the carrier
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 8, 8, 8]))
        self.assertFalse(any(u.unit_type == 'Aircraft Carrier' for u in gs.territories[3].units))
        self.assertIn(plane, gs.territories[3].units)
        gs.phase = Phase.NONCOMBAT_MOVE
        engine.process_return_to_base('NAA')
        opts = engine.legal_noncombat_move_options('NAA')
        self.assertIn(1, opts[plane.unit_id]['destinations'], 'it may fly home to land')
        self.assertEqual(engine.aircraft_that_must_land('NAA'), {plane.unit_id: 'no_carrier'})

    def test_a_full_carrier_is_no_base_to_return_to(self):
        carrier, planes = make_unit('Aircraft Carrier', 'NAA'), fighters(4)
        gs, engine = game({2: [carrier] + planes[:3], 3: [planes[3]]})  # it attacked in zone 3 and survived
        planes[3].combat_move_origin, planes[3].based_on_carrier = 2, carrier.unit_id
        engine.process_return_to_base('NAA')
        self.assertIn(planes[3], gs.territories[3].units)  # left where it is...
        self.assertIn(1, engine.legal_noncombat_move_options('NAA')[planes[3].unit_id]['destinations'])  # ...free to fly home
        self.assertEqual(engine.aircraft_that_must_land('NAA'), {planes[3].unit_id: 'no_carrier'})


class TestCrashes(unittest.TestCase):
    def test_every_aircraft_left_with_nowhere_to_land_crashes_and_is_logged(self):
        carrier, planes = make_unit('Aircraft Carrier', 'NAA'), fighters(5)
        gs, engine = game({2: [carrier] + planes[:4], 5: [planes[4]], 4: [make_unit('Fighter', 'NAA')]})
        engine.process_return_to_base('NAA')
        must = engine.aircraft_that_must_land('NAA')
        self.assertEqual(sorted(must.values()), ['carrier_full', 'hostile_land', 'no_carrier'])
        engine.confirm_noncombat_moves('NAA')
        lost = [u for e in engine.turn_log.events if e['kind'] == 'aircraft_lost' for u in e['units']]
        self.assertEqual(sorted(u['reason'] for u in lost), ['carrier_full', 'hostile_land', 'no_carrier'])
        self.assertEqual(sum(1 for u in gs.territories[2].units if u.unit_type == 'Fighter'), 3)
        self.assertFalse(gs.territories[5].units)
        self.assertFalse(gs.territories[4].units)

    def test_allied_land_is_a_safe_landing(self):
        gs, engine = game({4: [make_unit('Fighter', 'NAA')]})
        gs.factions['NAA'].alliance = gs.factions['AAC'].alliance = 'A'
        self.assertEqual(engine.aircraft_that_must_land('NAA'), {})


if __name__ == '__main__':
    unittest.main()
