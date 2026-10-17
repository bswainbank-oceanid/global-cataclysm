"""A bot decides whether it will withdraw from its alliance as its turn's planning begins
(RandomBot._maybe_roll_treacherous_intent: the whole decision, held in pending_treacherous_withdrawal until Diplomacy)
-- and, planning to, its defensive objectives treat the allies it is leaving as enemies (Planner.turning_on)."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.random_bot import RandomBot
from engine.bots.strategy_settings import load_settings
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# 1: NAA's Strategic Center, next to 2: UE's land, where UE's army stands. 3: GPC's, beyond.
TERRITORIES = {1: {'type': 'land', 'value': 5, 'strategic_center': True, 'name': 'Capital'},
               2: {'type': 'land', 'value': 2, 'name': 'March'}, 3: {'type': 'land', 'value': 2, 'name': 'Far'}}
ADJACENCY = {1: [2], 2: [1, 3], 3: [2]}


def game(behavior='loyal'):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    gs = make_state(data, {1: 'NAA', 2: 'UE', 3: 'GPC'},
                    {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT},
                    units_by_territory={1: [make_unit('Infantry', 'NAA')], 2: [make_unit('Armor', 'UE') for _ in range(4)]},
                    phase=Phase.PURCHASE)
    gs.active_faction = 'NAA'
    for f in ('NAA', 'UE'):
        gs.factions[f].alliance = 'pact'
    gs.factions['NAA'].alliance_behavior = behavior
    return GameEngine(gs, data), gs


def planner(engine):
    return Planner(engine, 'NAA', load_settings(), 'Strategic', random.Random(3), 'full', 1500)


class TestLeavingAnAlliance(unittest.TestCase):
    def test_an_ally_is_no_threat_while_staying(self):
        engine, gs = game()
        self.assertEqual(planner(engine).threats(1), {})

    def test_the_allies_being_left_are_threats_to_defend_against(self):
        engine, gs = game()
        gs.factions['NAA'].pending_treacherous_withdrawal = True  # (the plan, made as the turn began)
        p = planner(engine)
        self.assertTrue(p.leaving)
        self.assertEqual(sorted(p.threats(1)), ['UE'])
        self.assertLess(p.hold_chance(1, p.defenders_at(1, claimed_only=False)), 0.5)  # 4 Armor on 1 Infantry
        self.assertIsNotNone(gs.factions['NAA'].alliance)  # (only planning: still allied until Diplomacy)

    def test_the_decision_is_made_as_the_turn_begins(self):
        engine, gs = game('opportunistic')
        gs.factions['NAA'].treasury_mpc, gs.factions['UE'].treasury_mpc = 100, 10  # much stronger than its ally
        RandomBot(engine, 'NAA', rng=random.Random(1))._maybe_roll_treacherous_intent()
        self.assertTrue(gs.factions['NAA'].pending_treacherous_withdrawal)
        engine, gs = game('loyal')
        RandomBot(engine, 'NAA', rng=random.Random(1))._maybe_roll_treacherous_intent()
        self.assertFalse(gs.factions['NAA'].pending_treacherous_withdrawal)

    def test_diplomacy_carries_out_the_plan_made_at_the_start(self):
        engine, gs = game('loyal')
        gs.factions['NAA'].pending_treacherous_withdrawal = True
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        self.assertEqual(bot.plan_alliance_phase()['action'], 'withdraw')
        self.assertFalse(gs.factions['NAA'].pending_treacherous_withdrawal)  # (used up)


if __name__ == '__main__':
    unittest.main()
