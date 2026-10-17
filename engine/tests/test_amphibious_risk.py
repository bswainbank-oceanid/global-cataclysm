"""The strategy planner's risk check for an amphibious attack: a Mechanized Infantry whose path fights through an
enemy-held sea zone must live through that sea battle before it lands (GameEngine._land_after_sea_battle), so the
attack's odds allow for the Transports the sea battle may sink (Planner.through_sea_battles)."""
import random
import unittest

from engine.bots import battle_sim
from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# 2 (NAA's land) -- 3 (sea) -- 6 (AAC's land, an AAC Infantry)
TERRITORIES = {2: {'type': 'land', 'value': 2, 'name': 'Home'}, 3: {'type': 'sea', 'name': 'Strait'},
               6: {'type': 'land', 'value': 1, 'name': 'Shore'}}
ADJACENCY = {2: [3], 3: [2, 6], 6: [3]}


def setup(fleet):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    mechs = [make_unit('Mechanized Infantry', 'NAA') for _ in range(3)]
    gs = make_state(data, {2: 'NAA', 6: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
                    units_by_territory={2: mechs, 3: [make_unit(t, 'AAC') for t in fleet], 6: [make_unit('Infantry', 'AAC')]},
                    phase=Phase.COMBAT_MOVE)
    gs.active_faction = 'NAA'
    engine = GameEngine(gs, data)
    planner = Planner(engine, 'NAA', load_settings(), 'Strategic', random.Random(5), 'full', 1500)
    planner._attack_candidates(6, ('Land',))  # (finds each one's path -- and the sea battle on it)
    land_odds = lambda us: planner.attacker_odds(us, planner.enemies_at(6), 'land', fast=False)  # noqa: E731
    return planner, mechs, land_odds


class TestAmphibiousRisk(unittest.TestCase):
    def test_open_water_changes_nothing(self):
        planner, mechs, land_odds = setup([])
        self.assertIsNone(planner._fight_at.get(mechs[0].unit_id))
        self.assertEqual(planner.through_sea_battles(mechs, land_odds), land_odds(mechs))

    def test_a_fleet_on_the_way_lowers_the_odds_by_the_transports_it_may_sink(self):
        planner, mechs, land_odds = setup(['Cruiser', 'Cruiser', 'Cruiser'])
        self.assertEqual(planner._fight_at[mechs[0].unit_id], 3)
        plain = land_odds(mechs)
        allowing = planner.through_sea_battles(mechs, land_odds)
        self.assertGreater(plain, 0.9)  # three against one Infantry, if they all got there
        self.assertLess(allowing, plain - 0.2)  # but three Cruisers sink Transports first
        sea = battle_sim.survivors(mechs, planner.enemies_at(3), 'sea', planner.unit_defs, planner.rules,
                                   random.Random(1), samples=200)
        self.assertAlmostEqual(sum(sea.values()), 1.0)
        self.assertGreater(sea.get(frozenset(), 0.0), 0.0)  # (sometimes none of them makes it)


if __name__ == '__main__':
    unittest.main()
