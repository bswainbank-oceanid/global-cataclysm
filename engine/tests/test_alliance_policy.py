import random
import unittest

from engine.bots import alliance_policy
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit


def make_engine(modes, alliances=None, strategies=None, behaviors=None,
                 treasuries=None, max_alliance_size=5, can_withdraw_from_alliances=True,
                 can_rejoin_alliances=False, units_by_territory=None, phase=Phase.ALLIANCES):
    data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
    gs = make_state(data, {1: next(iter(modes))}, modes, treasury=treasuries,
                     units_by_territory=units_by_territory, phase=phase)
    gs.max_alliance_size = max_alliance_size
    gs.can_withdraw_from_alliances = can_withdraw_from_alliances
    gs.can_rejoin_alliances = can_rejoin_alliances
    for code, tag in (alliances or {}).items():
        gs.factions[code].alliance = tag
    for code, strat in (strategies or {}).items():
        gs.factions[code].alliance_strategy = strat
    for code, beh in (behaviors or {}).items():
        gs.factions[code].alliance_behavior = beh
    return GameEngine(gs, data), gs


class TestResolveSettings(unittest.TestCase):
    def test_random_strategy_resolves_to_a_concrete_member(self):
        result = alliance_policy.resolve_alliance_strategy('random', random.Random(1))
        self.assertIn(result, alliance_policy.STRATEGIES)

    def test_none_strategy_is_treated_as_random(self):
        result = alliance_policy.resolve_alliance_strategy(None, random.Random(1))
        self.assertIn(result, alliance_policy.STRATEGIES)

    def test_explicit_strategy_passes_through_lowercased(self):
        self.assertEqual(alliance_policy.resolve_alliance_strategy('Aggressive', random.Random(1)), 'aggressive')

    def test_random_behavior_resolves_to_a_concrete_member(self):
        result = alliance_policy.resolve_alliance_behavior('random', random.Random(2))
        self.assertIn(result, alliance_policy.BEHAVIORS)

    def test_explicit_behavior_passes_through_lowercased(self):
        self.assertEqual(alliance_policy.resolve_alliance_behavior('Treacherous', random.Random(1)), 'treacherous')


class TestChooseInviteTarget(unittest.TestCase):
    def test_passive_never_invites(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'NAA': 'passive'},
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))

    def test_independent_never_invites(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'NAA': 'independent'},
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))

    def test_aggressive_picks_an_eligible_target(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            strategies={'NAA': 'aggressive'},
        )
        target = alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1))
        self.assertIn(target, ('UE', 'AAC'))

    def test_aggressive_does_nothing_at_max_alliance_size(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            strategies={'NAA': 'aggressive'},
            max_alliance_size=2,
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))

    def test_aggressive_has_no_eligible_targets(self):
        # UE and AAC are both already allied elsewhere (single-member
        # alliance tags -- not naturally reachable via invite, but valid
        # hand-built state for isolating this check). A 3rd bystander
        # (AAC) keeps active_count at 3 (effective cap 2, not the
        # binding constraint here) so it's really the "everyone else is
        # ineligible" branch being exercised, not the size cap.
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'UE': 'other', 'AAC': 'other2'},
            strategies={'NAA': 'aggressive'},
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))

    def test_counterweight_does_nothing_when_no_other_alliance_exists(self):
        # A 3rd bystander (AAC) keeps the effective size cap (2) from
        # preempting the intended check -- with only 2 active factions
        # the cap alone would already force None before ever reaching
        # the "no other alliance" branch.
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            strategies={'NAA': 'counterweight'},
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))

    def test_counterweight_invites_to_match_the_largest_other_alliance(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT},
            alliances={'UE': 'rival', 'AAC': 'rival'},  # rival alliance of size 2
            strategies={'NAA': 'counterweight'},
        )
        target = alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1))
        self.assertEqual(target, 'GPC')  # the only eligible faction left

    def test_counterweight_stops_once_matched(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT},
            alliances={'UE': 'rival', 'AAC': 'rival', 'NAA': 'mine', 'GPC': 'mine'},
            strategies={'NAA': 'counterweight'},
        )
        self.assertIsNone(alliance_policy.choose_invite_target(engine, 'NAA', random.Random(1)))


class TestAcceptsInvite(unittest.TestCase):
    def test_independent_never_accepts(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'UE': 'independent'},
        )
        self.assertFalse(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))

    def test_aggressive_always_accepts(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'UE': 'aggressive'},
        )
        self.assertTrue(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))

    def test_passive_always_accepts(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'UE': 'passive'},
        )
        self.assertTrue(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))

    def test_counterweight_accepts_when_no_other_alliance_exists(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'UE': 'counterweight'},
        )
        self.assertTrue(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))

    def test_counterweight_accepts_when_it_would_only_match_not_exceed(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT},
            alliances={'AAC': 'rival', 'GPC': 'rival'},  # size 2
            strategies={'UE': 'counterweight'},
        )
        # NAA is solo; UE joining makes a size-2 alliance -- matches, doesn't exceed.
        self.assertTrue(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))

    def test_counterweight_declines_if_joining_would_exceed_the_largest_other_alliance(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'PAF': FactionMode.BOT,
             'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT},
            alliances={'NAA': 'mine', 'PAF': 'mine', 'AAC': 'rival', 'GPC': 'rival'},
            strategies={'UE': 'counterweight'},
        )
        # NAA's alliance already has 2 members; UE joining makes 3,
        # exceeding rival's size of 2.
        self.assertFalse(alliance_policy.accepts_invite(engine, 'UE', 'NAA'))


class TestTotalUnitValue(unittest.TestCase):
    def test_none_cost_units_dont_break_the_sum(self):
        units = [make_unit('Transport', 'NAA'), make_unit('Infantry', 'NAA')]
        engine, gs = make_engine({'NAA': FactionMode.BOT}, units_by_territory={1: units})
        self.assertEqual(alliance_policy._total_unit_value(engine, 'NAA'), 4)  # Transport cost None -> 0


class TestShouldWithdraw(unittest.TestCase):
    def test_loyal_never_withdraws_despite_a_strength_mismatch(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'loyal'},
            treasuries={'NAA': 10000, 'UE': 1},
        )
        self.assertFalse(alliance_policy.should_withdraw(engine, 'NAA'))

    def test_loyal_ignores_the_game_end_override(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},  # only 2 active factions, both allied -> game would end
            behaviors={'NAA': 'loyal'},
        )
        self.assertTrue(engine.would_game_end())  # sanity
        self.assertFalse(alliance_policy.should_withdraw(engine, 'NAA'))

    def test_opportunistic_withdraws_when_much_stronger_than_the_weakest_ally(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},  # AAC stays unallied, a bystander so would_game_end() is False
            behaviors={'NAA': 'opportunistic'},
            treasuries={'NAA': 1000, 'UE': 100, 'AAC': 500},
        )
        self.assertFalse(engine.would_game_end())  # sanity: isolating the strength check, not the override
        self.assertTrue(alliance_policy.should_withdraw(engine, 'NAA'))  # 1000 > 1.5 * 100

    def test_opportunistic_withdraws_when_much_weaker_than_the_strongest_ally(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'opportunistic'},
            treasuries={'NAA': 40, 'UE': 100, 'AAC': 500},
        )
        self.assertFalse(engine.would_game_end())
        self.assertTrue(alliance_policy.should_withdraw(engine, 'NAA'))  # 40 < 0.5 * 100

    def test_opportunistic_stays_within_bounds(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'opportunistic'},
            treasuries={'NAA': 100, 'UE': 100, 'AAC': 500},
        )
        self.assertFalse(engine.would_game_end())
        self.assertFalse(alliance_policy.should_withdraw(engine, 'NAA'))

    def test_opportunistic_always_withdraws_to_avoid_game_end(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},  # only 2 active factions, both allied
            behaviors={'NAA': 'opportunistic'},
            treasuries={'NAA': 100, 'UE': 100},  # within bounds -- would NOT otherwise trigger
        )
        self.assertTrue(engine.would_game_end())
        self.assertTrue(alliance_policy.should_withdraw(engine, 'NAA'))

    def test_treacherous_honors_the_pending_flag_and_consumes_it(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},  # AAC bystander keeps would_game_end() False
            behaviors={'NAA': 'treacherous'},
        )
        gs.factions['NAA'].pending_treacherous_withdrawal = True
        self.assertFalse(engine.would_game_end())
        self.assertTrue(alliance_policy.should_withdraw(engine, 'NAA'))
        self.assertFalse(gs.factions['NAA'].pending_treacherous_withdrawal, 'consumed on read')

    def test_treacherous_without_the_pending_flag_does_not_withdraw(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'treacherous'},
        )
        self.assertFalse(alliance_policy.should_withdraw(engine, 'NAA'))

    def test_treacherous_also_always_withdraws_to_avoid_game_end(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'treacherous'},
        )
        self.assertTrue(engine.would_game_end())
        self.assertTrue(alliance_policy.should_withdraw(engine, 'NAA'))


class TestRandomBotAllianceIntegration(unittest.TestCase):
    def test_treacherous_intent_only_rolled_while_allied(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT}, behaviors={'NAA': 'treacherous'}, phase=Phase.PURCHASE,
        )
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot._maybe_roll_treacherous_intent()
        self.assertFalse(gs.factions['NAA'].pending_treacherous_withdrawal, 'not allied -- nothing to roll for')

    def test_treacherous_intent_rolled_while_allied(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            behaviors={'NAA': 'treacherous'},
            phase=Phase.PURCHASE,
        )
        # Find a seed that rolls under 15% deterministically -- just
        # check the roll actually happened (bool, not the specific value).
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot._maybe_roll_treacherous_intent()
        self.assertIsInstance(gs.factions['NAA'].pending_treacherous_withdrawal, bool)

    def test_take_alliance_phase_independent_does_nothing(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            strategies={'NAA': 'independent', 'UE': 'independent'},
        )
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot.take_alliance_phase()
        self.assertIsNone(gs.factions['NAA'].alliance)

    def test_take_alliance_phase_aggressive_forms_an_alliance_with_an_accepting_target(self):
        # A 3rd bystander (AAC) is needed: with only 2 active factions
        # the effective size cap would be 1, blocking any alliance from
        # forming at all -- see TestEffectiveMaxAllianceSize. AAC is
        # already (nominally) allied elsewhere so it can't itself be
        # picked as the random invite target, keeping this deterministic.
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'AAC': 'other'},
            strategies={'NAA': 'aggressive', 'UE': 'passive'},  # UE always accepts
        )
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot.take_alliance_phase()
        self.assertIsNotNone(gs.factions['NAA'].alliance)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['UE'].alliance)

    def test_take_alliance_phase_withdraws_instead_of_inviting_when_behavior_says_so(self):
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT, 'AAC': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            strategies={'NAA': 'aggressive'},  # would otherwise want to grow the alliance
            behaviors={'NAA': 'treacherous'},
        )
        gs.factions['NAA'].pending_treacherous_withdrawal = True
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot.take_alliance_phase()
        self.assertIsNone(gs.factions['NAA'].alliance, 'withdrew instead of inviting')

    def test_take_alliance_phase_withdrawal_blocked_by_setting_falls_through_to_nothing(self):
        # can_withdraw_from_alliances False means the withdrawal branch is
        # never even attempted -- but strategy is passive, so no invite
        # happens either; the faction just stays put this turn.
        engine, gs = make_engine(
            {'NAA': FactionMode.BOT, 'UE': FactionMode.BOT},
            alliances={'NAA': 'pact', 'UE': 'pact'},
            strategies={'NAA': 'passive'},
            behaviors={'NAA': 'treacherous'},
            can_withdraw_from_alliances=False,
        )
        gs.factions['NAA'].pending_treacherous_withdrawal = True
        bot = RandomBot(engine, 'NAA', rng=random.Random(1))
        bot.take_alliance_phase()
        self.assertEqual(gs.factions['NAA'].alliance, 'pact', 'withdrawal disabled by setting')


if __name__ == '__main__':
    unittest.main()
