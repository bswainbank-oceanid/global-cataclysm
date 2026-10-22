"""Asking to join an alliance: a faction in no alliance may invite one that is in an alliance. That faction alone
answers -- by its own alliance strategy -- and if it accepts, the inviter joins its alliance, allied with every
member (the others have no say). The alliance size limit and the rejoin rule still apply."""
import random
import unittest

from engine.bots import alliance_policy
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state

FACTIONS = ('AAC', 'NAA', 'UER', 'EU', 'GPC')


def game(alliances, max_size=3, can_rejoin=False):
    data = FakeData(territories={1: {'type': 'land', 'value': 1}}, adjacency={})
    gs = make_state(data, {1: 'AAC'}, {f: FactionMode.BOT for f in FACTIONS}, phase=Phase.DIPLOMACY)
    gs.active_faction = 'AAC'
    gs.max_alliance_size = max_size
    gs.can_rejoin_alliances = can_rejoin
    for f, tag in alliances.items():
        gs.factions[f].alliance = tag
    return GameEngine(gs, data), gs


class TestAskingToJoin(unittest.TestCase):
    def test_a_faction_in_an_alliance_can_be_asked(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2', 'EU': 'A1', 'GPC': 'A1'})
        self.assertEqual(engine.legal_alliance_options('AAC')['eligible_invite_targets'], ['EU', 'GPC', 'NAA', 'UER'])

    def test_on_a_yes_the_inviter_joins_and_is_allied_with_every_member(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2'})
        self.assertTrue(engine.invite_to_alliance('AAC', 'NAA', True))
        self.assertEqual(gs.factions['AAC'].alliance, 'A2')
        self.assertEqual(engine.alliance_members('AAC'), {'AAC', 'NAA', 'UER'})  # UER had no say
        self.assertEqual(gs.factions['UER'].alliance, 'A2')

    def test_on_a_no_nothing_changes(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2'})
        self.assertFalse(engine.invite_to_alliance('AAC', 'NAA', False))
        self.assertIsNone(gs.factions['AAC'].alliance)
        self.assertEqual(engine.alliance_members('NAA'), {'NAA', 'UER'})

    def test_not_into_a_full_alliance(self):
        engine, gs = game({'EU': 'A1', 'GPC': 'A1', 'UER': 'A1'})
        self.assertNotIn('EU', engine.legal_alliance_options('AAC')['eligible_invite_targets'])
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('AAC', 'EU', True)

    def test_not_from_inside_another_alliance(self):
        engine, gs = game({'AAC': 'A3', 'EU': 'A3', 'NAA': 'A2', 'UER': 'A2'})
        self.assertNotIn('NAA', engine.legal_alliance_options('AAC')['eligible_invite_targets'])
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('AAC', 'NAA', True)

    def test_not_into_an_alliance_holding_one_it_left(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2'})
        gs.factions['AAC'].former_allies.add('UER')
        gs.factions['UER'].former_allies.add('AAC')
        self.assertNotIn('NAA', engine.legal_alliance_options('AAC')['eligible_invite_targets'])
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('AAC', 'NAA', True)


class TestTheAnswerFollowsTheInvitedFactionsStrategy(unittest.TestCase):
    def test_passive_says_yes_independent_no(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2'})
        gs.factions['NAA'].alliance_strategy = 'passive'
        self.assertTrue(alliance_policy.accepts_invite(engine, 'NAA', 'AAC'))
        gs.factions['NAA'].alliance_strategy = 'independent'
        self.assertFalse(alliance_policy.accepts_invite(engine, 'NAA', 'AAC'))

    def test_counterweight_judges_the_alliance_that_would_result(self):
        # NAA+UER with AAC would be 3, bigger than the other alliance (2): no
        engine, gs = game({'NAA': 'A2', 'UER': 'A2', 'EU': 'A1', 'GPC': 'A1'}, max_size=4)
        gs.factions['NAA'].alliance_strategy = 'counterweight'
        self.assertFalse(alliance_policy.accepts_invite(engine, 'NAA', 'AAC'))
        # NAA's alliance of one (hand-built) with AAC would be 2, matching the other alliance: yes
        engine, gs = game({'NAA': 'A2', 'EU': 'A1', 'GPC': 'A1'}, max_size=4)
        gs.factions['NAA'].alliance_strategy = 'counterweight'
        self.assertTrue(alliance_policy.accepts_invite(engine, 'NAA', 'AAC'))

    def test_a_bot_may_ask_to_join_too(self):
        engine, gs = game({'NAA': 'A2', 'UER': 'A2', 'EU': 'A1', 'GPC': 'A1'})
        gs.factions['AAC'].alliance_strategy = 'aggressive'
        picks = {alliance_policy.choose_invite_target(engine, 'AAC', random.Random(s)) for s in range(30)}
        self.assertEqual(picks, {'EU', 'GPC', 'NAA', 'UER'})


if __name__ == '__main__':
    unittest.main()
