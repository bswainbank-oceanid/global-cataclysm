"""The Strategy Log (engine/bots/strategy_log.py): a strategy bot's turns, as the watcher's stepper runs
them, log strategy_turn_start / strategy_phase / strategy_turn_end events with every choice tied to an
objective; random bots log none; and the log never changes the game."""
import unittest

from server.lobby import build_session


def bot(strategy='random', ai='strategy'):
    return {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': strategy, 'behavior': 'random', 'ai': ai}


NC = {'mode': 'NONCOMBATANT', 'faction': 'random', 'alliance': 0}


def play(seats, turns, seed=7):
    session, _ = build_session({'seats': seats + [NC] * (6 - len(seats)), 'seed': seed, 'allow_combat_first_turn': True})
    session.handle_message({'type': 'watch'})
    events = []
    for _ in range(600):
        for m in session.handle_message({'type': 'next'}):
            if m.get('type') == 'phase_result':
                events += m['events']
        if sum(1 for e in events if e['kind'] == 'start_of_turn') > turns:
            break
    return session, events


class TestStrategyLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, cls.events = play([bot(), bot(), bot(ai='random')], 5)
        cls.strategy = [e for e in cls.events if e['kind'].startswith('strategy_')]
        cls.strategy_bots = {f for f, b in cls.session.bots.items() if type(b).__name__ == 'StrategyBot'}

    def test_only_strategy_bots_log(self):
        self.assertTrue(self.strategy)
        self.assertEqual({e['faction'] for e in self.strategy}, self.strategy_bots)

    def test_a_turn_is_start_phases_end_in_order(self):
        f = sorted(self.strategy_bots)[0]
        kinds = [(e['kind'], e.get('phase')) for e in self.strategy if e['faction'] == f]
        self.assertEqual(kinds[:5], [('strategy_turn_start', None), ('strategy_phase', 'PURCHASE'),
                                     ('strategy_phase', 'COMBAT_MOVE'), ('strategy_phase', 'NONCOMBAT_MOVE'),
                                     ('strategy_turn_end', None)])

    def test_turn_start_and_end_carry_the_round(self):
        for e in self.strategy:
            if e['kind'] in ('strategy_turn_start', 'strategy_turn_end'):
                self.assertIsInstance(e['round'], int)
                self.assertGreaterEqual(e['round'], 1)

    def test_purchases_deployed_elsewhere_are_listed_under_deploy(self):
        from engine.bots.strategy_log import StrategyLogger
        f = sorted(self.strategy_bots)[0]
        log = StrategyLogger(self.session.engine, self.session.bots[f])
        moved = {'kind': 'deploy_redirected', 'faction': f, 'from': 40, 'to': 39,
                 'units': [{'unit_type': 'Infantry', 'qty': 6}], 'reason': 'territory_lost'}
        other = dict(moved, faction='someone else')
        self.assertIsNone(log.deploy([{'kind': 'unit_deployed', 'faction': f}]))
        e = log.deploy([moved, other, {'kind': 'income_collected', 'faction': f}])
        self.assertEqual((e['kind'], e['phase'], e['deploy_changes']), ('strategy_phase', 'DEPLOY_INCOME', [moved]))
        self.assertEqual(log.turn_end()['review']['deploy_changes'], [moved])

    def test_stats_and_changes(self):
        starts = [e for e in self.strategy if e['kind'] == 'strategy_turn_start']
        self.assertTrue(all(set(e['stats']) == {'territory_mpc', 'unit_value', 'unit_count', 'scs'} for e in starts))
        f = starts[0]['faction']
        mine = [e for e in starts if e['faction'] == f]
        self.assertIsNone(mine[0]['change'], 'nothing to compare the first turn with')
        if len(mine) > 1:
            self.assertEqual(mine[1]['change'], {k: mine[1]['stats'][k] - mine[0]['stats'][k] for k in mine[0]['stats']})
        end = next(e for e in self.strategy if e['kind'] == 'strategy_turn_end' and e['faction'] == f)
        self.assertEqual(end['change'], {k: end['stats'][k] - mine[0]['stats'][k] for k in end['stats']})

    def test_every_choice_names_an_objective_in_the_planning_order(self):
        for e in self.strategy:
            if e['kind'] != 'strategy_phase':
                continue
            for c in e['choices']:
                o = c['objective']
                self.assertNotEqual(o['id'], 'unplanned', c)
                self.assertIsInstance(o['no'], int, c)
                self.assertTrue(o['name'])

    def test_purchases_match_what_was_bought(self):
        for e in self.strategy:
            if e['kind'] == 'strategy_phase' and e['phase'] == 'PURCHASE':
                i = self.events.index(e)
                bought = next(x for x in reversed(self.events[:i]) if x['kind'] == 'purchase' and x['faction'] == e['faction'])
                self.assertEqual(sum(c['qty'] for c in e['choices']), sum(o['qty'] for o in bought['orders']))

    def test_the_review_lists_idle_units_and_objectives_without_resources(self):
        ends = [e for e in self.strategy if e['kind'] == 'strategy_turn_end']
        self.assertTrue(ends)
        for e in ends:
            r = e['review']
            self.assertEqual(set(r), {'purchase', 'combat', 'noncombat', 'rejected', 'deploy_changes', 'idle_units',
                                      'no_resources'})
            for n in r['no_resources']:
                self.assertTrue(n['reasons'], n)
                self.assertTrue(all(w['reason'] for w in n['reasons']))
        self.assertTrue(any(e['review']['no_resources'] for e in ends), 'some objective always goes without early on')

    def test_the_log_does_not_change_the_game(self):
        from unittest import mock
        from server.stepper import PhaseStepper
        with mock.patch.object(PhaseStepper, '_strategy_log', lambda self, faction: None):
            _, events = play([bot(), bot(), bot(ai='random')], 5)
        self.assertFalse([e for e in events if e['kind'].startswith('strategy_')])
        self.assertEqual(events, [e for e in self.events if not e['kind'].startswith('strategy_')])

if __name__ == '__main__':
    unittest.main()
