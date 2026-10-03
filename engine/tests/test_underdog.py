"""The Underdog alliance strategy and behaviour (engine/bots/alliance_policy.py): a group's strength is its total
income and total unit value, against the strongest other group on the board (each alliance one group, each
unallied player its own). It joins only an alliance weaker than that on both counts (under 100% of it with a
human in the alliance, under 130% with bots only), invites only factions that keep the alliance under 100%,
and plans -- like Treacherous -- to withdraw once its alliance is above it on either count."""
import random
import unittest
from unittest import mock

from engine.bots import alliance_policy as policy
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.state import FactionMode
from engine.tests.test_engine import FakeData, make_state

FACTIONS = ('NAA', 'UE', 'GPC', 'PAF')


def game(strength, humans=(), alliances=None):
    """`strength`: {faction: (income, unit value)}; `alliances`: {faction: tag}."""
    data = FakeData(territories={1: {'type': 'land', 'value': 1}}, adjacency={1: []})
    modes = {f: FactionMode.HUMAN if f in humans else FactionMode.BOT for f in FACTIONS}
    gs = make_state(data, {}, modes)
    gs.max_alliance_size = 3
    for f, tag in (alliances or {}).items():
        gs.factions[f].alliance = tag
    for f in FACTIONS:
        if f not in humans:
            gs.factions[f].alliance_strategy = 'underdog'
            gs.factions[f].alliance_behavior = 'underdog'
    engine = GameEngine(gs, data)
    patch = mock.patch.object(policy, '_group_strength', lambda engine, members: (
        sum(strength[m][0] for m in members), sum(strength[m][1] for m in members)))
    patch.start()
    return engine, gs, patch


class UnderdogTest(unittest.TestCase):
    def setUp(self):
        self.patches = []

    def tearDown(self):
        for p in reversed(self.patches):  # (last patched, first undone: else an earlier stub would be put back)
            p.stop()

    def game(self, *args, **kw):
        engine, gs, patch = game(*args, **kw)
        self.patches.append(patch)
        return engine, gs


class TestJoining(UnderdogTest):
    def test_with_bots_it_joins_under_130_percent_of_the_strongest_other(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (17, 17), 'PAF': (5, 5)})
        self.assertTrue(policy.accepts_invite(engine, 'NAA', 'UE'))   # 20 < 1.3 x 17
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (15, 15), 'PAF': (5, 5)})
        self.assertFalse(policy.accepts_invite(engine, 'NAA', 'UE'))  # 20 >= 1.3 x 15

    def test_with_a_human_it_must_stay_weaker(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (21, 21), 'PAF': (5, 5)}, humans=('UE',))
        self.assertTrue(policy.accepts_invite(engine, 'NAA', 'UE'))   # 20 < 21
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (19, 19), 'PAF': (5, 5)}, humans=('UE',))
        self.assertFalse(policy.accepts_invite(engine, 'NAA', 'UE'))  # under 130%, but a human is in it

    def test_both_measures_must_be_under(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (25, 10), 'PAF': (5, 5)})
        self.assertFalse(policy.accepts_invite(engine, 'NAA', 'UE'))  # income fine, unit value 20 >= 13

    def test_an_alliance_counts_as_one_group(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (9, 9), 'PAF': (9, 9)},
                              alliances={'GPC': 'A1', 'PAF': 'A1'})
        self.assertTrue(policy.accepts_invite(engine, 'NAA', 'UE'))   # 20 < 1.3 x 18 (GPC + PAF together)


class TestInviting(UnderdogTest):
    def test_it_invites_only_factions_that_keep_the_alliance_under_100_percent(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (30, 30), 'PAF': (12, 12)}, humans=('PAF',))
        picks = {policy.choose_invite_target(engine, 'NAA', random.Random(s)) for s in range(20)}
        self.assertEqual(picks, {'UE', 'PAF'})  # GPC would make the favourite; a human may be asked
        self.assertFalse(policy.may_invite(engine, 'NAA', 'GPC'))

    def test_nobody_when_every_partner_would_make_it_the_favourite(self):
        engine, _ = self.game({'NAA': (20, 20), 'UE': (20, 20), 'GPC': (21, 21), 'PAF': (20, 20)})
        self.assertIsNone(policy.choose_invite_target(engine, 'NAA', random.Random(1)))


class TestWithdrawing(UnderdogTest):
    def test_with_bots_it_leaves_above_130_percent_on_either_count(self):
        allied = {'NAA': 'A1', 'UE': 'A1'}
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (16, 16), 'PAF': (5, 5)}, alliances=allied)
        self.assertFalse(policy.underdog_wants_out(engine, 'NAA'))  # 20 <= 1.3 x 16
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (15, 15), 'PAF': (5, 5)}, alliances=allied)
        self.assertTrue(policy.underdog_wants_out(engine, 'NAA'))   # 20 > 19.5
        engine, _ = self.game({'NAA': (10, 5), 'UE': (10, 5), 'GPC': (15, 30), 'PAF': (5, 5)}, alliances=allied)
        self.assertTrue(policy.underdog_wants_out(engine, 'NAA'))   # income alone is enough

    def test_with_a_human_it_leaves_once_stronger_than_every_other_group(self):
        engine, _ = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (19, 19), 'PAF': (5, 5)},
                              humans=('UE',), alliances={'NAA': 'A1', 'UE': 'A1'})
        self.assertTrue(policy.underdog_wants_out(engine, 'NAA'))

    def test_it_plans_at_turn_start_and_withdraws_at_diplomacy(self):
        engine, gs = self.game({'NAA': (10, 10), 'UE': (10, 10), 'GPC': (15, 15), 'PAF': (5, 5)},
                               alliances={'NAA': 'A1', 'UE': 'A1'})
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot._maybe_roll_treacherous_intent()
        self.assertTrue(gs.factions['NAA'].pending_treacherous_withdrawal)  # what drives the treasonous moves
        self.assertTrue(policy.should_withdraw(engine, 'NAA'))

    def test_the_names_are_offered(self):
        self.assertIn('underdog', policy.STRATEGIES)
        self.assertIn('underdog', policy.BEHAVIORS)
        self.assertIn('underdog', policy._CONCRETE_STRATEGIES)  # a Variable bot's re-roll can land on it
        self.assertIn('underdog', policy._CONCRETE_BEHAVIORS)


if __name__ == '__main__':
    unittest.main()
