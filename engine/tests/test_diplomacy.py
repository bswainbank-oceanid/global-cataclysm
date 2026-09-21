import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state


def world(values, max_alliance_size=3, modes=None):
    """One land territory per faction, worth `values[code]`, all uncontested, at the Diplomacy phase of
    the first faction. Income is the value, so 10 vs 4 or less lets the first surrender the second."""
    codes = list(values)
    data = FakeData(territories={i + 1: {'type': 'land', 'value': v, 'faction': c} for i, (c, v) in enumerate(values.items())}, adjacency={})
    gs = make_state(data, {i + 1: c for i, c in enumerate(codes)},
                    {c: (modes or {}).get(c, FactionMode.BOT) for c in codes}, phase=Phase.DIPLOMACY)
    gs.active_faction = codes[0]
    gs.max_alliance_size = max_alliance_size
    engine = GameEngine(gs, data)
    return gs, engine, RandomBot(engine, codes[0], rng=random.Random(1))


class TestBotDiplomacy(unittest.TestCase):
    def test_a_bot_asks_before_it_eliminates_and_a_bot_never_declines(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 6})
        gs.factions['AAC'].alliance_strategy = 'independent'   # would turn every ordinary invitation down
        plan = bot.plan_diplomacy_phase()
        self.assertEqual((plan['action'], plan['target'], plan['accepts'], plan['demand_if_declined']), ('invite', 'AAC', True, True))
        bot.commit_diplomacy_phase(plan)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['AAC'].alliance)
        self.assertIsNotNone(gs.factions['AAC'].alliance)
        self.assertFalse(gs.factions['AAC'].eliminated)

    def test_a_human_who_declines_is_forced_to_surrender(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 6}, modes={'AAC': FactionMode.HUMAN})
        plan = bot.plan_diplomacy_phase()
        self.assertEqual((plan['action'], plan['target'], plan['accepts']), ('invite', 'AAC', None))  # only the player can say
        declined = dict(plan, accepts=False)
        bot.commit_diplomacy_phase(declined)
        self.assertTrue(gs.factions['AAC'].eliminated)

    def test_a_human_who_accepts_is_not_eliminated(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 6}, modes={'AAC': FactionMode.HUMAN})
        bot.commit_diplomacy_phase(dict(bot.plan_diplomacy_phase(), accepts=True))
        self.assertFalse(gs.factions['AAC'].eliminated)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['AAC'].alliance)

    def test_when_it_cannot_ask_it_eliminates_at_once(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 6}, max_alliance_size=1)
        plan = bot.plan_diplomacy_phase()
        self.assertEqual(plan['demands'], ['AAC'])
        self.assertNotIn('demand_if_declined', plan)
        bot.commit_diplomacy_phase(plan)
        self.assertTrue(gs.factions['AAC'].eliminated)
        self.assertFalse(gs.factions['UE'].eliminated)

    def test_it_asks_one_faction_a_turn_and_the_rest_wait(self):
        gs, engine, bot = world({'NAA': 20, 'AAC': 2, 'UE': 4, 'GPC': 12})
        plan = bot.plan_diplomacy_phase()
        self.assertEqual(plan['action'], 'invite')
        self.assertIn(plan['target'], ('AAC', 'UE'))
        self.assertEqual(plan['demands'], [])   # the other one is asked on a later turn, not eliminated unasked
        bot.commit_diplomacy_phase(plan)
        self.assertFalse(any(gs.factions[c].eliminated for c in ('AAC', 'UE')))

    def test_a_bot_never_demands_an_allys_surrender_unless_it_can_win(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 8})
        gs.factions['NAA'].alliance = gs.factions['AAC'].alliance = 'pact'
        plan = bot.plan_diplomacy_phase()
        self.assertEqual(plan.get('demands'), [])
        self.assertNotEqual((plan['action'], plan.get('target')), ('invite', 'AAC'))
        self.assertFalse(plan.get('win'))
        bot.commit_diplomacy_phase(plan)
        self.assertFalse(gs.factions['AAC'].eliminated)

    def test_a_bot_that_could_win_alone_eliminates_everyone_even_allies(self):
        gs, engine, bot = world({'NAA': 20, 'AAC': 2, 'UE': 3})
        gs.factions['NAA'].alliance = gs.factions['AAC'].alliance = 'pact'
        plan = bot.plan_diplomacy_phase()
        self.assertTrue(plan['win'])
        self.assertEqual(sorted(plan['demands']), ['AAC', 'UE'])
        self.assertEqual(plan['action'], 'none')
        bot.commit_diplomacy_phase(plan)
        self.assertEqual(gs.active_factions(), ['NAA'])
        self.assertTrue(engine.process_game_end_check('NAA'))

    def test_a_bot_with_nothing_to_demand_just_plays_its_alliance_policy(self):
        gs, engine, bot = world({'NAA': 5, 'AAC': 5, 'UE': 5})
        plan = bot.plan_diplomacy_phase()
        self.assertEqual(plan['demands'], [])
        self.assertNotIn('demand_if_declined', plan)

    def test_a_bot_that_left_an_alliance_can_then_demand_its_former_ally(self):
        gs, engine, bot = world({'NAA': 10, 'AAC': 2, 'UE': 8})
        gs.factions['NAA'].alliance = gs.factions['AAC'].alliance = 'pact'
        gs.factions['NAA'].former_allies.add('AAC')
        gs.factions['AAC'].former_allies.add('NAA')
        gs.factions['NAA'].alliance = None      # it withdrew earlier (a later turn's decision)
        gs.factions['AAC'].alliance = None
        gs.can_rejoin_alliances = False         # so it cannot ask AAC back in: it is forced to surrender at once
        plan = bot.plan_diplomacy_phase()
        self.assertEqual(plan['demands'], ['AAC'])


if __name__ == '__main__':
    unittest.main()
