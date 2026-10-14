"""The strategy bots' two sets of strategy profiles (engine/game_config.py's bot sets): the standard set and an
alternate one -- a copy to alter and test against it -- chosen per seat by the bot type 'strategy_alt'."""
import json
import unittest

from engine.bots.strategy_bot import StrategyBot
from engine.bots.strategy_settings import load_settings
from server import persist
from server.lobby import build_session, check_settings
from server.tests.test_persist import bot, seat


def mixed(seed=31):
    return {'seats': [bot('strategy'), bot('strategy_alt'), seat('NONCOMBATANT'), seat('NONCOMBATANT'),
                      seat('NONCOMBATANT'), seat('NONCOMBATANT')], 'seed': seed}


class TestBotSets(unittest.TestCase):
    def test_the_alternate_set_is_a_separate_copy_of_the_standard_one(self):
        standard, alternate = load_settings(None, 'standard'), load_settings(None, 'alternate')
        self.assertIsNot(standard, alternate)
        for field in ('thresholds', 'strategy_weights', 'unit_weights', 'distance_costs', 'primary_order', 'final_order',
                      'secondary', 'styles', 'secondary_budget_share', 'options'):
            self.assertEqual(getattr(standard, field), getattr(alternate, field), field)

    def test_both_sets_fill_gaps_again_before_pursuing_leftovers(self):
        from engine.bots.planner import Planner
        session, _ = build_session(mixed(34))
        for b in session.bots.values():
            planner = Planner(session.engine, b.faction, b.settings, b.base_style, b.rng, 'full', 50)
            planner.run()
            planned = [o['id'] for o in planner.objective_order]
            self.assertTrue({'hold_sc', 'capture_sc'} <= set(planned), planned)
            self.assertEqual((planned.count('fill_gaps'), planned[-2:]), (2, ['fill_gaps', 'pursue_leftovers']), planned)

    def test_a_seat_chooses_its_set(self):
        self.assertEqual(check_settings(mixed()), [])
        session, seats = build_session(mixed())
        sets = sorted(b.bot_set for b in session.bots.values() if isinstance(b, StrategyBot))
        self.assertEqual(sets, ['alternate', 'standard'])
        by_ai = {s['ai']: session.bots[s['faction']].bot_set for s in seats if s['mode'] == 'BOT'}
        self.assertEqual(by_ai, {'strategy': 'standard', 'strategy_alt': 'alternate'})

    def test_a_saved_game_keeps_each_bots_set(self):
        session, seats = build_session(mixed(32))
        game = persist.RecordedGame(session, persist.setup_for(session, seats, mixed(32)))
        game.handle_message({'type': 'watch'})
        loaded = persist.load(json.loads(json.dumps(game.doc())))
        self.assertEqual({f: b.bot_set for f, b in loaded.bots.items()},
                         {f: b.bot_set for f, b in session.bots.items()})

    def test_the_game_report_names_the_alternate_set(self):
        from server.report import _bot_info
        session, _ = build_session(mixed(33))
        kinds = sorted(_bot_info(b)[0] for b in session.bots.values())
        self.assertEqual(kinds, ['Strategy', 'Strategy (Alt)'])


if __name__ == '__main__':
    unittest.main()
