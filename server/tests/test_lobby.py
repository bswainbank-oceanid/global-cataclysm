import random
import unittest

from engine.state import FactionMode
from server.host import GameHost
from server.lobby import LobbyError, build_session, check_settings, resolve_settings

FACTIONS = ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')


def seat(mode='NEUTRAL', faction='random', alliance=0, strategy='random', behavior='random'):
    return {'mode': mode, 'faction': faction, 'alliance': alliance, 'strategy': strategy, 'behavior': behavior}


def settings(*seats, **kw):
    seats = list(seats) + [seat()] * (6 - len(seats))
    return {'seats': seats, **kw}


class TestSettingsChecks(unittest.TestCase):
    def test_a_normal_one_human_one_bot_game_is_valid(self):
        self.assertEqual(check_settings(settings(seat('HUMAN'), seat('BOT'))), [])

    def test_at_least_two_players_are_needed(self):
        self.assertTrue(check_settings(settings(seat('HUMAN'))))
        self.assertTrue(check_settings(settings(seat('BOT'), seat('DEFENSIVE'), seat('NEUTRAL'))))
        self.assertTrue(check_settings(settings()))

    def test_zero_humans_is_allowed_and_more_than_one_is_not(self):
        self.assertEqual(check_settings(settings(seat('BOT'), seat('BOT'))), [])
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('HUMAN'), seat('BOT'))))

    def test_explicit_factions_must_be_distinct(self):
        problems = check_settings(settings(seat('HUMAN', 'NAA'), seat('BOT', 'NAA')))
        self.assertTrue(any('NAA' in p for p in problems))

    def test_unknown_values_are_rejected(self):
        self.assertTrue(check_settings(settings(seat('WIZARD'), seat('BOT'))))
        self.assertTrue(check_settings(settings(seat('HUMAN', 'XYZ'), seat('BOT'))))
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT', strategy='sneaky'))))
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT', behavior='shy'))))

    def test_there_must_be_six_seats(self):
        self.assertTrue(check_settings({'seats': [seat('BOT'), seat('BOT')]}))

    def test_an_alliance_needs_two_members_and_cannot_be_every_player(self):
        lone = check_settings(settings(seat('HUMAN', alliance=1), seat('BOT'), seat('BOT')))
        self.assertTrue(any('only one member' in p for p in lone))
        everyone = check_settings(settings(seat('HUMAN', alliance=1), seat('BOT', alliance=1)))
        self.assertTrue(any('every player' in p for p in everyone))
        ok = check_settings(settings(seat('HUMAN', alliance=1), seat('BOT', alliance=1), seat('BOT')))
        self.assertEqual(ok, [])

    def test_alliances_on_non_player_seats_are_ignored(self):
        self.assertEqual(check_settings(settings(seat('HUMAN'), seat('BOT'), seat('NEUTRAL', alliance=2))), [])

    def test_all_problems_are_reported_together(self):
        with self.assertRaises(LobbyError) as ctx:
            resolve_settings(settings(seat('HUMAN', 'NAA'), seat('HUMAN', 'NAA')))
        self.assertGreaterEqual(len(ctx.exception.problems), 2)


class TestResolveAndBuild(unittest.TestCase):
    def test_every_faction_ends_up_in_exactly_one_seat(self):
        for seed in range(20):
            assignments, _, _ = resolve_settings(settings(
                seat('HUMAN', 'GPC'), seat('BOT'), seat('DEFENSIVE'), seat('NEUTRAL'), seat('BOT', 'NAA')), random.Random(seed))
            self.assertEqual(sorted(a['faction'] for a in assignments), sorted(FACTIONS))
            self.assertEqual(assignments[0]['faction'], 'GPC')
            self.assertEqual(assignments[4]['faction'], 'NAA')

    def test_random_picks_vary_with_the_seed(self):
        seen = {resolve_settings(settings(seat('BOT'), seat('BOT')), random.Random(s))[0][0]['faction'] for s in range(30)}
        self.assertGreater(len(seen), 2)

    def test_alliance_numbers_become_faction_groups(self):
        _, groups, _ = resolve_settings(settings(
            seat('HUMAN', 'NAA', alliance=2), seat('BOT', 'UE', alliance=2), seat('BOT', 'GPC', alliance=1),
            seat('BOT', 'AAC', alliance=1), seat('BOT', 'PAF')), random.Random(1))
        self.assertEqual(sorted(sorted(g) for g in groups), [['AAC', 'GPC'], ['NAA', 'UE']])

    def test_the_built_game_matches_the_settings(self):
        session, seats = build_session(settings(
            seat('HUMAN', 'NAA', alliance=1), seat('BOT', 'UE', alliance=1, strategy='aggressive', behavior='loyal'),
            seat('BOT', 'GPC'), seat('DEFENSIVE', 'UER'), seat('NEUTRAL', 'PAF'), seat('NEUTRAL', 'AAC'),
            randomize_order=False), random.Random(3))
        gs = session.engine.game_state
        self.assertEqual(gs.factions['NAA'].mode, FactionMode.HUMAN)
        self.assertEqual(gs.factions['UER'].mode, FactionMode.DEFENSIVE)
        self.assertEqual(gs.factions['PAF'].mode, FactionMode.NEUTRAL)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['UE'].alliance)
        self.assertIsNone(gs.factions['GPC'].alliance)
        self.assertEqual(gs.factions['UE'].alliance_strategy, 'aggressive')
        self.assertEqual(gs.factions['UE'].alliance_behavior, 'loyal')
        self.assertIn(gs.factions['GPC'].alliance_strategy, ('aggressive', 'passive', 'counterweight', 'independent', 'variable'))
        self.assertEqual(set(session.bots), {'UE', 'GPC'})
        self.assertEqual([s['seat'] for s in seats], [1, 2, 3, 4, 5, 6])

    def test_a_three_member_alliance_fits_the_default_size_limit(self):
        session, _ = build_session(settings(
            seat('BOT', 'NAA', alliance=1), seat('BOT', 'UE', alliance=1), seat('BOT', 'GPC', alliance=1),
            seat('BOT', 'AAC')), random.Random(1))
        self.assertEqual(session.engine.game_state.max_alliance_size, 3)

    def test_turn_order_is_randomised_by_default_and_can_be_fixed(self):
        firsts = set()
        for seed in range(30):
            session, _ = build_session(settings(seat('BOT'), seat('BOT'), seat('BOT')), random.Random(seed))
            firsts.add(session.engine.game_state.active_faction)
        self.assertGreater(len(firsts), 1)
        firsts = set()
        for seed in range(10):
            session, _ = build_session(settings(seat('BOT', 'GPC'), seat('BOT', 'NAA'), randomize_order=False), random.Random(seed))
            firsts.add(session.engine.game_state.active_faction)
        self.assertEqual(len(firsts), 1)  # the same first faction every time

    def test_a_zero_human_game_can_be_watched_to_its_first_step(self):
        session, _ = build_session(settings(seat('BOT'), seat('BOT')), random.Random(1))
        messages = session.handle_message({'type': 'watch'})
        self.assertEqual([m['type'] for m in messages], ['state', 'phase_queue'])
        messages = session.handle_message({'type': 'next'})
        self.assertNotIn('error', [m['type'] for m in messages])


class TestGameHost(unittest.TestCase):
    def test_an_idle_host_offers_the_launch_screen_and_refuses_game_messages(self):
        host = GameHost()
        self.assertEqual(host.lobby_message(), {'type': 'lobby', 'game_running': False, 'seats': []})
        direct, broadcast, joins = host.handle({'type': 'next'})
        self.assertEqual((direct[0]['type'], broadcast, joins), ('error', [], False))

    def test_new_game_builds_and_starts_the_game_for_the_sender_and_watchers(self):
        host = GameHost()
        direct, broadcast, joins = host.handle({'type': 'new_game', 'settings': settings(seat('HUMAN'), seat('BOT'))})
        self.assertEqual(direct, [])
        self.assertTrue(joins)
        self.assertEqual([m['type'] for m in broadcast], ['game_started', 'state', 'phase_queue'])
        self.assertEqual(len(broadcast[0]['seats']), 6)
        self.assertTrue(host.lobby_message()['game_running'])

    def test_bad_settings_are_an_error_to_the_sender_and_leave_the_running_game(self):
        host = GameHost()
        host.handle({'type': 'new_game', 'settings': settings(seat('BOT'), seat('BOT'))})
        session = host.session
        direct, broadcast, joins = host.handle({'type': 'new_game', 'settings': settings(seat('HUMAN'))})
        self.assertEqual((direct[0]['type'], broadcast, joins), ('error', [], False))
        self.assertTrue(direct[0]['problems'])
        self.assertIs(host.session, session)

    def test_a_second_new_game_replaces_the_first(self):
        host = GameHost()
        host.handle({'type': 'new_game', 'settings': settings(seat('BOT'), seat('BOT'))})
        first = host.session
        host.handle({'type': 'new_game', 'settings': settings(seat('BOT'), seat('BOT'))})
        self.assertIsNot(host.session, first)

    def test_game_messages_reach_the_running_session(self):
        host = GameHost()
        host.handle({'type': 'new_game', 'settings': settings(seat('BOT'), seat('BOT'))})
        direct, broadcast, _ = host.handle({'type': 'next'})
        self.assertEqual(direct, [])
        self.assertIn('phase_result', [m['type'] for m in broadcast])


if __name__ == '__main__':
    unittest.main()


class TestAllianceRuleSettings(unittest.TestCase):
    def _game(self, **kw):
        session, _ = build_session(settings(seat('HUMAN'), seat('BOT'), **kw), random.Random(1))
        return session.engine.game_state

    def test_the_defaults_are_withdraw_yes_rejoin_no(self):
        gs = self._game()
        self.assertTrue(gs.can_withdraw_from_alliances)
        self.assertFalse(gs.can_rejoin_alliances)

    def test_the_settings_reach_the_game(self):
        gs = self._game(can_withdraw=False, can_rejoin=True)
        self.assertFalse(gs.can_withdraw_from_alliances)
        self.assertTrue(gs.can_rejoin_alliances)

    def test_they_are_in_the_state_the_client_receives(self):
        session, _ = build_session(settings(seat('HUMAN'), seat('BOT'), can_withdraw=False, can_rejoin=True), random.Random(1))
        state = session.handle_message({'type': 'watch'})[0]['game_state']
        self.assertFalse(state['can_withdraw_from_alliances'])
        self.assertTrue(state['can_rejoin_alliances'])

    def test_non_boolean_values_are_rejected(self):
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT'), can_withdraw='yes')))
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT'), can_rejoin=1)))


class TestBotAi(unittest.TestCase):
    def test_bots_play_the_heuristic_ai_unless_told_otherwise(self):
        from engine.bots.random_bot import RandomBot
        from engine.bots.strategy_bot import StrategyBot
        session, seats = build_session(settings(seat('HUMAN'), seat('BOT'), {**seat('BOT'), 'ai': 'random'}, seat('BOT', 'AAC')), random.Random(1))
        kinds = {a['faction']: type(session.bots[a['faction']]) for a in seats if a['mode'] == 'BOT'}
        self.assertEqual(sorted(k.__name__ for k in kinds.values()), ['RandomBot', 'StrategyBot', 'StrategyBot'])
        self.assertEqual([a['ai'] for a in seats if a['mode'] == 'BOT'].count('random'), 1)

    def test_an_unknown_ai_is_rejected(self):
        self.assertTrue(check_settings(settings(seat('HUMAN'), {**seat('BOT'), 'ai': 'genius'})))
        self.assertEqual(check_settings(settings(seat('HUMAN'), {**seat('BOT'), 'ai': 'random'})), [])


class TestMaxAllianceSize(unittest.TestCase):
    def four_players(self, **kw):
        return settings(seat('HUMAN'), seat('BOT'), seat('BOT'), seat('BOT'), **kw)

    def test_the_default_is_three(self):
        session, _ = build_session(self.four_players(), random.Random(1))
        self.assertEqual(session.engine.game_state.max_alliance_size, 3)

    def test_the_chosen_size_reaches_the_game(self):
        session, _ = build_session(self.four_players(max_alliance_size=2), random.Random(1))
        self.assertEqual(session.engine.game_state.max_alliance_size, 2)
        state = session.handle_message({'type': 'watch'})[0]['game_state']
        self.assertEqual(state['max_alliance_size'], 2)

    def test_it_can_be_at_most_the_number_of_players_minus_one(self):
        self.assertEqual(check_settings(self.four_players(max_alliance_size=3)), [])
        self.assertTrue(check_settings(self.four_players(max_alliance_size=4)))
        self.assertEqual(check_settings(self.four_players(max_alliance_size=1)), [])  # 1 = no alliances
        self.assertTrue(check_settings(self.four_players(max_alliance_size=0)))
        three = settings(seat('HUMAN'), seat('BOT'), seat('BOT'), max_alliance_size=2)
        self.assertEqual(check_settings(three), [])
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT'), seat('BOT'), max_alliance_size=3)))

    def test_a_two_player_game_takes_the_default_without_complaint(self):
        self.assertEqual(check_settings(settings(seat('HUMAN'), seat('BOT'))), [])

    def test_it_must_be_a_whole_number(self):
        self.assertTrue(check_settings(self.four_players(max_alliance_size='3')))
        self.assertTrue(check_settings(self.four_players(max_alliance_size=True)))

    def test_a_starting_alliance_may_not_exceed_it(self):
        big = self.four_players(max_alliance_size=2)
        for i in range(3):
            big['seats'][i]['alliance'] = 1
        problems = check_settings(big)
        self.assertTrue(any('maximum alliance size' in p for p in problems), problems)
        big['max_alliance_size'] = 3
        self.assertEqual(check_settings(big), [])


class TestStartupOptions(unittest.TestCase):
    def game(self, **kw):
        session, _ = build_session(settings(seat('HUMAN'), seat('BOT'), seat('BOT'), **kw), random.Random(1))
        return session, session.engine.game_state

    def test_the_first_turn_defaults_are_no_combat_yes_non_combat(self):
        _, gs = self.game()
        self.assertFalse(gs.allow_combat_moves_first_turn)
        self.assertTrue(gs.allow_noncombat_moves_first_turn)

    def test_the_first_turn_options_reach_the_game(self):
        _, gs = self.game(allow_combat_first_turn=True, allow_noncombat_first_turn=False)
        self.assertTrue(gs.allow_combat_moves_first_turn)
        self.assertFalse(gs.allow_noncombat_moves_first_turn)

    def test_they_must_be_true_or_false(self):
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT'), allow_combat_first_turn='yes')))
        self.assertTrue(check_settings(settings(seat('HUMAN'), seat('BOT'), allow_noncombat_first_turn=1)))

    def test_a_maximum_alliance_size_of_one_means_no_alliances(self):
        _, gs = self.game(max_alliance_size=1)
        self.assertFalse(gs.alliances_enabled)
        _, gs = self.game()
        self.assertTrue(gs.alliances_enabled)

    def test_a_two_player_game_may_pick_one_too(self):
        self.assertEqual(check_settings(settings(seat('HUMAN'), seat('BOT'), max_alliance_size=1)), [])

    def test_no_starting_alliance_fits_a_maximum_of_one(self):
        s = settings(seat('HUMAN', alliance=1), seat('BOT', alliance=1), seat('BOT'), max_alliance_size=1)
        self.assertTrue(any('maximum alliance size' in p for p in check_settings(s)))
