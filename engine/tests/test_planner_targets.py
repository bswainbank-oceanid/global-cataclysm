"""The strategy planner's targeting rules: an Infantry or Armor finds its way to a Strategic Center over land
only; only Mechanized Infantry are bought toward an island from elsewhere; Submarines take no part in the
Strategic Center objectives; Expand Territory looks at empty territory first."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# A mainland of four -- 1 (NAA's Strategic Center), 2 and 6 (NAA's), 5 (GPC's) -- and 4, an island Strategic
# Center of GPC's across sea zone 3.
TERRITORIES = {
    1: {'type': 'land', 'value': 5, 'strategic_center': True, 'name': 'Capital'},
    2: {'type': 'land', 'value': 2, 'name': 'Farm'},
    3: {'type': 'sea', 'name': 'Strait'},
    4: {'type': 'land', 'value': 3, 'strategic_center': True, 'name': 'Isle'},
    5: {'type': 'land', 'value': 1, 'name': 'Border'},
    6: {'type': 'land', 'value': 2, 'name': 'Hills'},
}
ADJACENCY = {1: [2, 3, 6], 2: [1, 6], 3: [1, 4], 4: [3], 5: [6], 6: [1, 2, 5]}


def game(units=None, owners=None):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    gs = make_state(data, owners or {1: 'NAA', 2: 'NAA', 6: 'NAA', 4: 'GPC', 5: 'GPC'},
                    {'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT}, units_by_territory=units or {})
    gs.active_faction = 'NAA'
    return GameEngine(gs, data), gs


def planner(engine, style='Strategic', seed=3):
    return Planner(engine, 'NAA', load_settings(), style, random.Random(seed), 'full', 1500)


class TestLandPaths(unittest.TestCase):
    def test_infantry_and_armor_are_land_bound(self):
        p = planner(game()[0])
        bound = {t: p.land_bound(make_unit(t, 'NAA')) for t in ('Infantry', 'Armor', 'Mechanized Infantry', 'Fighter')}
        self.assertEqual(bound, {'Infantry': True, 'Armor': True, 'Mechanized Infantry': False, 'Fighter': False})

    def test_a_land_only_path_never_crosses_the_sea(self):
        p = planner(game()[0])
        self.assertIn(1, p.costs_from(4)[0])                                 # by sea, the island is reachable...
        self.assertEqual(set(p.costs_from(4, land_only=True)[0]), {4})         # ...by land, from nowhere
        self.assertEqual(set(p.costs_from(5, land_only=True)[0]), {1, 2, 5, 6})

    def test_an_infantry_does_not_march_on_an_island_it_cannot_reach(self):
        inf = make_unit('Infantry', 'NAA')
        engine, gs = game({2: [inf]}, owners={1: 'NAA', 2: 'NAA', 6: 'NAA', 4: 'GPC', 5: 'NAA'})  # the island is the only target
        p = planner(engine)
        p._begin_objective('pursue_sc_1')
        p.objective_pursue_sc(0, [0])
        self.assertNotIn(inf.unit_id, p.moves_nc)
        self.assertNotIn(inf.unit_id, p.unit_why)


class TestIslandPurchases(unittest.TestCase):
    def bought(self, target, n):
        engine, gs = game({3: [make_unit('Cruiser', 'GPC')]})  # (no buying straight into the strait: the land spots)
        p = planner(engine)
        p._target = target
        for _ in range(n):
            p.buy_toward(p.costs_from(target)[0], ('Land',), 'test')
        return [t for (t, _), q in p.purchases.items() for _ in range(q)]

    def test_only_mechanized_infantry_are_bought_toward_an_island(self):
        types = self.bought(4, 6)
        self.assertTrue(types)
        self.assertEqual(set(types), {'Mechanized Infantry'})

    def test_toward_the_mainland_the_usual_odds_apply(self):
        self.assertNotEqual(set(self.bought(5, 10)), {'Mechanized Infantry'})


class TestNoSubmarinesForStrategicCenters(unittest.TestCase):
    def test_pursuing_an_sc_neither_moves_nor_buys_a_submarine(self):
        sub, fighter = make_unit('Submarine', 'NAA'), make_unit('Fighter', 'NAA')
        engine, gs = game({3: [sub], 1: [fighter]})
        for oid, run in (('pursue_sc_1', lambda p: p.objective_pursue_sc(0, [0])),
                         ('pursue_leftovers', lambda p: p.objective_pursue_leftovers())):
            p = planner(engine)
            p._begin_objective(oid)
            run(p)
            self.assertNotIn(sub.unit_id, p.unit_why, oid)
            self.assertFalse([t for (t, _) in p.purchases if t == 'Submarine'], oid)


class TestExpandTerritory(unittest.TestCase):
    def setUp(self):
        # NAA's infantry at 6 can reach both 5 (empty, value 1) and 2... here 2 is GPC's and strongly held.
        self.engine, self.gs = game({6: [make_unit('Infantry', 'NAA') for _ in range(3)],
                                     2: [make_unit('Armor', 'GPC') for _ in range(6)]},
                                    owners={1: 'NAA', 6: 'NAA', 2: 'GPC', 4: 'GPC', 5: 'GPC'})

    def test_empty_territory_is_looked_at_first_and_taken(self):
        p = planner(self.engine, 'Expansive')
        p._begin_objective('expand_territory')
        p.objective_expand_territory()
        first = p.attempts[0]
        self.assertEqual((first['target'], first['outcome']), (5, 'pursued'))  # before the more valuable 2

    def test_a_target_whose_units_are_all_committed_says_so(self):
        p = planner(self.engine, 'Expansive')
        p._begin_objective('expand_territory')
        for u in self.gs.territories[6].units:
            p.claim(u, 'hold_sc')
        p.assault(5, 'expand_territory', (0.5, 0.9))
        self.assertEqual(p.attempts[-1]['reason'], 'every unit that could reach it is committed elsewhere')


class TestHoldChance(unittest.TestCase):
    """A defense is judged by the next turn's real battle: captured only within its 3 rounds, and only with a
    land unit of the attacker's left standing."""

    def test_surviving_the_three_rounds_holds_it(self):
        # 12 Infantry at NAA's capital against 6 Armor next door: a long fight lost, but seldom within 3 rounds
        engine, gs = game({1: [make_unit('Infantry', 'NAA') for _ in range(12)],
                           2: [make_unit('Armor', 'GPC') for _ in range(6)]},
                          owners={1: 'NAA', 2: 'GPC', 6: 'NAA', 4: 'GPC', 5: 'GPC'})
        p = planner(engine)
        defenders = p.defenders_at(1, claimed_only=False)
        three = p.hold_chance(1, defenders, fast=False)
        attackers = p.threats(1)['GPC']
        unlimited = 1.0 - p.attacker_odds(attackers, defenders, 'land', fast=False)
        self.assertGreater(three, unlimited + 0.2)

    def test_aircraft_that_clear_the_defenders_do_not_capture(self):
        # a Fighter wing and one Infantry against a lone defender: often the Infantry falls too, and then
        # nothing takes the territory
        engine, gs = game({1: [make_unit('Infantry', 'NAA')],
                           2: [make_unit('Fighter', 'GPC') for _ in range(4)] + [make_unit('Infantry', 'GPC')]},
                          owners={1: 'NAA', 2: 'GPC', 6: 'NAA', 4: 'GPC', 5: 'GPC'})
        p = planner(engine)
        self.assertGreater(p.hold_chance(1, p.defenders_at(1, claimed_only=False), fast=False), 0.05)


class TestControlOceans(unittest.TestCase):
    """Enemy Transports at sea are Control Oceans targets like ships, for aircraft from land too; aircraft left
    with nothing to do land on a carrier, the one nearest the enemy."""

    # 20: NAA's island, with sea zone 21 beside it; 22: GPC's coast beyond 21. Sea zones 23 and 24 run away
    # from the enemy (21 - 23 - 24), and 25 is NAA's mainland, next to 24.
    MAP = {20: {'type': 'land', 'value': 2, 'name': 'Isle'}, 21: {'type': 'sea', 'name': 'Strait'},
           22: {'type': 'land', 'value': 3, 'name': 'Coast'}, 23: {'type': 'sea', 'name': 'Bay'},
           24: {'type': 'sea', 'name': 'Far Sea'}, 25: {'type': 'land', 'value': 3, 'name': 'Home'}}
    ADJ = {20: [21, 23], 21: [20, 22, 23], 22: [21], 23: [20, 21, 24], 24: [23, 25], 25: [24]}

    def plan(self, units, mode='full'):
        data = FakeData(territories=self.MAP, adjacency=self.ADJ)
        gs = make_state(data, {20: 'NAA', 22: 'GPC', 25: 'NAA'}, {'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT},
                        units_by_territory=units)
        gs.active_faction = 'NAA'
        p = Planner(GameEngine(gs, data), 'NAA', load_settings(), 'Controlling', random.Random(2), mode, 1500)
        p._begin_objective('control_oceans')
        p.objective_control_oceans()
        return p

    def test_an_island_fighter_attacks_an_enemy_transport(self):
        fighter = make_unit('Fighter', 'NAA')
        p = self.plan({20: [fighter], 21: [make_unit('Mechanized Infantry', 'GPC')], 22: [make_unit('Infantry', 'GPC')]})
        self.assertEqual(p.moves_combat.get(fighter.unit_id), [20, 21])
        self.assertEqual(p.unit_why[fighter.unit_id], ('control_oceans', 21))

    def test_an_idle_aircraft_lands_on_the_carrier_nearest_the_enemy(self):
        fighter = make_unit('Fighter', 'NAA')
        p = self.plan({25: [fighter], 23: [make_unit('Aircraft Carrier', 'NAA')], 24: [make_unit('Aircraft Carrier', 'NAA')],
                       22: [make_unit('Infantry', 'GPC')]}, mode='noncombat')
        self.assertEqual(p.moves_nc.get(fighter.unit_id), 23)  # 23 is nearer the enemy at 22 than 24 is

    def test_a_carrier_takes_no_more_than_it_holds_and_one_already_aboard_stays(self):
        carrier = make_unit('Aircraft Carrier', 'NAA')
        aboard = [make_unit('Fighter', 'NAA') for _ in range(2)]
        idle = [make_unit('Fighter', 'NAA') for _ in range(2)]
        p = self.plan({23: [carrier] + aboard, 25: idle, 22: [make_unit('Infantry', 'GPC')]}, mode='noncombat')
        self.assertFalse(any(u.unit_id in p.moves_nc for u in aboard))
        self.assertEqual(sum(1 for u in idle if p.moves_nc.get(u.unit_id) == 23), 1)  # room for one more


class TestLandingOnAMovingCarrier(unittest.TestCase):
    """An idle aircraft may land on a carrier the plan moves this turn: where it starts (the aircraft's order
    first, and it rides along) or, out of reach of that, where it is going (the carrier's order first)."""

    # 30: NAA's land, on a chain of sea zones 31 - 32 - 33 - 34 - 35; 36: GPC's, beside 35.
    MAP = {30: {'type': 'land', 'value': 2, 'name': 'Port'}, 36: {'type': 'land', 'value': 2, 'name': 'Enemy'},
           **{i: {'type': 'sea', 'name': f'Sea {i}'} for i in range(31, 36)}}
    ADJ = {30: [31], 31: [30, 32], 32: [31, 33], 33: [32, 34], 34: [33, 35], 35: [34, 36], 36: [35]}

    def run_plan(self, carrier_at, carrier_to):
        fighter, carrier = make_unit('Fighter', 'NAA'), make_unit('Aircraft Carrier', 'NAA')
        data = FakeData(territories=self.MAP, adjacency=self.ADJ)
        gs = make_state(data, {30: 'NAA', 36: 'GPC'}, {'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT},
                        units_by_territory={30: [fighter], carrier_at: [carrier], 36: [make_unit('Infantry', 'GPC')]},
                        phase=Phase.NONCOMBAT_MOVE)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        p = Planner(engine, 'NAA', load_settings(), 'Controlling', random.Random(2), 'noncombat', 1500)
        p._begin_objective('control_oceans')
        p.claim(carrier, 'control_oceans', carrier_to)
        p.moves_nc[carrier.unit_id] = carrier_to  # the plan sails it
        p._land_idle_aircraft_on_carriers()
        engine.submit_noncombat_moves('NAA', p.noncombat_orders())
        engine.confirm_noncombat_moves('NAA')
        where = {u.unit_id: tid for tid, t in gs.territories.items() for u in t.units}
        return p, fighter, carrier, where

    def test_in_reach_of_its_start_the_aircraft_lands_first_and_rides_along(self):
        p, fighter, carrier, where = self.run_plan(31, 33)
        self.assertEqual((p.moves_nc[fighter.unit_id], p.nc_rank[fighter.unit_id]), (31, 0))
        self.assertEqual(p.noncombat_orders()[0].unit_id, fighter.unit_id)
        self.assertEqual((where[fighter.unit_id], where[carrier.unit_id]), (33, 33))

    def test_out_of_reach_of_its_start_the_aircraft_lands_where_it_is_going(self):
        p, fighter, carrier, where = self.run_plan(35, 33)  # 35 is 5 away; 33 is 3
        self.assertEqual((p.moves_nc[fighter.unit_id], p.nc_rank[fighter.unit_id]), (33, 2))
        self.assertEqual(p.noncombat_orders()[-1].unit_id, fighter.unit_id)
        self.assertEqual((where[fighter.unit_id], where[carrier.unit_id]), (33, 33))


class TestNonCombatOrderSequence(unittest.TestCase):
    def test_aircraft_move_before_a_carrier_can_sweep_them_along(self):
        # 20 NAA's island; 21 and 23 sea zones: the carrier sails 21 -> 23 while its fighter flies home to 20
        carrier, fighter = make_unit('Aircraft Carrier', 'NAA'), make_unit('Fighter', 'NAA')
        m = TestControlOceans
        data = FakeData(territories=m.MAP, adjacency=m.ADJ)
        gs = make_state(data, {20: 'NAA', 22: 'GPC', 25: 'NAA'}, {'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT},
                        units_by_territory={21: [carrier, fighter]}, phase=Phase.NONCOMBAT_MOVE)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        p = Planner(engine, 'NAA', load_settings(), 'Strategic', random.Random(1), 'noncombat', 1500)
        p.moves_nc = {carrier.unit_id: 23, fighter.unit_id: 20}
        self.assertEqual([o.unit_id for o in p.noncombat_orders()], [fighter.unit_id, carrier.unit_id])
        engine.submit_noncombat_moves('NAA', p.noncombat_orders())  # (the other way round, the fighter's is illegal)


class TestEmptyLandGrab(unittest.TestCase):
    """The primary objective: undefended enemy land in reach gets the cheapest land unit that can take it."""

    # 10 and 11 are NAA's, both next to 12 (GPC's, empty); 13 (GPC's) is two steps from 10 and holds an Armor.
    MAP = {10: {'type': 'land', 'value': 2, 'name': 'West'}, 11: {'type': 'land', 'value': 2, 'name': 'East'},
           12: {'type': 'land', 'value': 1, 'name': 'Gap'}, 13: {'type': 'land', 'value': 1, 'name': 'Fort'}}
    ADJ = {10: [11, 12], 11: [10, 12], 12: [10, 11, 13], 13: [12]}

    def plan(self, units, mode='full'):
        data = FakeData(territories=self.MAP, adjacency=self.ADJ)
        units.setdefault(13, [make_unit('Armor', 'GPC')])
        gs = make_state(data, {10: 'NAA', 11: 'NAA', 12: 'GPC', 13: 'GPC'},
                        {'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT}, units_by_territory=units)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        p = Planner(engine, 'NAA', load_settings(), 'Strategic', random.Random(1), mode, 1500)
        p._begin_objective('empty_land_grab')
        p.objective_empty_land_grab()
        return p, engine

    def test_the_cheapest_unit_that_can_reach_it_goes(self):
        armor, inf = make_unit('Armor', 'NAA'), make_unit('Infantry', 'NAA')
        p, engine = self.plan({10: [armor, inf]})
        self.assertEqual(p.moves_combat, {inf.unit_id: [10, 12]})
        self.assertEqual(p.unit_why[inf.unit_id], ('empty_land_grab', 12))
        self.assertFalse(p.purchases)
        engine.game_state.phase = Phase.COMBAT_MOVE
        engine.submit_combat_moves('NAA', p.combat_orders())  # a legal move

    def test_even_the_last_defender_goes(self):
        inf = make_unit('Infantry', 'NAA')
        p, _ = self.plan({11: [inf]})
        self.assertEqual(p.moves_combat, {inf.unit_id: [11, 12]})

    def test_ties_go_to_the_unit_that_leaves_the_most_behind(self):
        lone, pair = make_unit('Infantry', 'NAA'), [make_unit('Infantry', 'NAA') for _ in range(2)]
        p, _ = self.plan({11: [lone], 10: pair})
        self.assertEqual(len(p.moves_combat), 1)
        self.assertIn(next(iter(p.moves_combat)), {u.unit_id for u in pair})

    def test_defended_land_is_not_a_target_and_the_non_combat_pass_does_nothing(self):
        p, _ = self.plan({10: [make_unit('Infantry', 'NAA')]})
        self.assertNotIn(13, [a['target'] for a in p.attempts])
        p, _ = self.plan({10: [make_unit('Infantry', 'NAA')]}, mode='noncombat')
        self.assertFalse(p.moves_combat)


if __name__ == '__main__':
    unittest.main()
