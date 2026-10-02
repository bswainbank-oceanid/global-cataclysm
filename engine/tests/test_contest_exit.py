"""movement.contested_combat_move_rule: a unit that begins Combat Move in a contested territory or sea zone may
leave it only by the non-combat rules, at its combat-move range -- to its own, an ally's or a contested place,
never into enemy land or a new fight -- and that is its only move of the turn. The bots leave such units to
fight, except Transports, which head for safety."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from engine.engine import CombatMoveOrder, GameEngine, NonCombatMoveOrder
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# 1: NAA's, contested by AAC. 2 and 5: NAA's (5 two steps from 1, through 2). 3: AAC's, next to 1 and 2.
# 6: GPC's, next to 1 -- empty foreign land. 4: a sea zone off 1 and 2, contested; 7: open sea beside 4.
TERRITORIES = {1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 2}, 3: {'type': 'land', 'value': 2},
               4: {'type': 'sea'}, 5: {'type': 'land', 'value': 2}, 6: {'type': 'land', 'value': 1}, 7: {'type': 'sea'}}
for _tid, _t in TERRITORIES.items():
    _t['name'] = f'Space {_tid}'
ADJACENCY = {1: [2, 3, 4, 6], 2: [1, 3, 4, 5], 3: [1, 2], 4: [1, 2, 7], 5: [2], 6: [1], 7: [4]}


def board(units, contested=None):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    gs = make_state(data, {1: 'NAA', 2: 'NAA', 5: 'NAA', 3: 'AAC', 6: 'GPC'},
                    {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT},
                    units_by_territory=units, contested=contested or {1: {'NAA', 'AAC'}}, phase=Phase.COMBAT_MOVE)
    gs.active_faction = 'NAA'
    return GameEngine(gs, data), gs


class TestLeavingAContest(unittest.TestCase):
    def test_only_own_allied_or_contested_places_at_combat_range(self):
        inf = make_unit('Infantry', 'NAA')
        engine, gs = board({1: [inf, make_unit('Infantry', 'AAC')], 3: [make_unit('Infantry', 'AAC')]})
        dests = set(engine.legal_combat_move_options('NAA')[inf.unit_id]['destinations'])
        self.assertEqual(dests, {2})  # not enemy 3, not empty foreign 6; and 5 is beyond an Infantry's combat move

    def test_the_move_is_its_only_one_and_starts_no_fight(self):
        inf = make_unit('Infantry', 'NAA')
        engine, gs = board({1: [inf, make_unit('Infantry', 'AAC')]})
        engine.submit_combat_moves('NAA', [CombatMoveOrder(inf.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(inf, gs.territories[2].units)
        self.assertTrue(inf.has_moved_combat and inf.has_moved_noncombat)
        self.assertIsNone(gs.territories[2].contested_by)
        with self.assertRaises(ValueError):
            engine._execute_noncombat_moves([NonCombatMoveOrder(inf.unit_id, 5)], 'NAA', gs)

    def test_no_new_attack_from_a_contest(self):
        inf = make_unit('Infantry', 'NAA')
        engine, gs = board({1: [inf, make_unit('Infantry', 'AAC')], 3: [make_unit('Infantry', 'AAC')]})
        for target in (3, 6):
            with self.assertRaises(ValueError):
                engine.submit_combat_moves('NAA', [CombatMoveOrder(inf.unit_id, [1, target])])

    def test_a_unit_outside_any_contest_attacks_as_before(self):
        inf = make_unit('Infantry', 'NAA')
        engine, gs = board({2: [inf], 1: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'AAC')],
                            3: [make_unit('Infantry', 'AAC')]})
        self.assertIn(3, engine.legal_combat_move_options('NAA')[inf.unit_id]['destinations'])

    def test_a_mech_inf_cannot_drive_through_enemy_land(self):
        mech = make_unit('Mechanized Infantry', 'NAA')
        engine, gs = board({1: [mech, make_unit('Infantry', 'AAC')], 3: [make_unit('Infantry', 'AAC')]})
        paths = engine.legal_combat_move_options('NAA')[mech.unit_id]['destinations']
        self.assertEqual(paths[5], [1, 2, 5])  # its combat range of 2, by own land
        self.assertNotIn(3, paths)

    def test_an_aircraft_lands_by_the_non_combat_rules(self):
        fighter = make_unit('Fighter', 'NAA')
        engine, gs = board({1: [fighter, make_unit('Infantry', 'AAC')], 3: [make_unit('Infantry', 'AAC')]})
        dests = set(engine.legal_combat_move_options('NAA')[fighter.unit_id]['destinations'])
        self.assertIn(2, dests)
        self.assertIn(5, dests)
        self.assertNotIn(3, dests)  # an attack target for a combat move, not a landing

    def test_a_ship_leaves_a_contested_sea_zone_for_open_water(self):
        cruiser = make_unit('Cruiser', 'NAA')
        engine, gs = board({4: [cruiser, make_unit('Submarine', 'AAC')]}, contested={4: {'NAA', 'AAC'}})
        self.assertEqual(set(engine.legal_combat_move_options('NAA')[cruiser.unit_id]['destinations']), {7})


class TestQuietSeaContests(unittest.TestCase):
    """A sea zone's contest ends at Capture Territory once no two hostile sides have units there."""

    def capture(self, units):
        engine, gs = board(units, contested={4: {'NAA', 'AAC'}})
        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')
        return gs.territories[4].contested_by

    def test_an_empty_sea_zone_is_no_longer_contested(self):
        self.assertIsNone(self.capture({}))

    def test_nor_is_one_with_a_single_side_left(self):
        self.assertIsNone(self.capture({4: [make_unit('Cruiser', 'AAC')]}))

    def test_hostile_fleets_still_facing_each_other_stay_contested(self):
        self.assertEqual(self.capture({4: [make_unit('Cruiser', 'AAC'), make_unit('Cruiser', 'NAA')]}), {'NAA', 'AAC'})


class TestBotsInAContest(unittest.TestCase):
    def plan(self, units, contested):
        engine, gs = board(units, contested)
        gs.phase = Phase.PURCHASE
        p = Planner(engine, 'NAA', load_settings(), 'Expansive', random.Random(1), 'full', 1500)
        p.run()
        return p

    def test_a_transport_heads_for_friendly_land(self):
        transport = make_unit('Mechanized Infantry', 'NAA')
        p = self.plan({4: [transport, make_unit('Cruiser', 'AAC')], 3: [make_unit('Infantry', 'AAC')]},
                      {4: {'NAA', 'AAC'}})
        self.assertIn(p.moves_combat.get(transport.unit_id, [None])[-1], (1, 2))
        self.assertEqual(p.unit_why[transport.unit_id][0], 'transport_safety')

    def test_other_units_in_a_contest_are_left_to_fight(self):
        armor = make_unit('Armor', 'NAA')
        p = self.plan({1: [armor, make_unit('Infantry', 'AAC')], 6: []}, {1: {'NAA', 'AAC'}})
        self.assertNotIn(armor.unit_id, p.moves_combat)


if __name__ == '__main__':
    unittest.main()
