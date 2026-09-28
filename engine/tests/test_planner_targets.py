"""The strategy planner's targeting rules: an Infantry or Armor finds its way to a Strategic Center over land
only; only Mechanized Infantry are bought toward an island from elsewhere; Submarines take no part in the
Strategic Center objectives; Expand Territory looks at empty territory first."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from engine.engine import GameEngine
from engine.state import FactionMode
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


if __name__ == '__main__':
    unittest.main()
