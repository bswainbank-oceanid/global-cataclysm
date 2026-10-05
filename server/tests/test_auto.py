"""Auto mode (server/session.py): the game runs itself until it needs a human; broadcasts are numbered
and kept for clients catching up; and a saved auto game reloads exactly (server/persist.py)."""
import json
import unittest
from unittest import mock

from server import persist
from server.lobby import build_session
from server.tests.test_persist import bot, fingerprint, seat


def auto_game(settings):
    session, seats = build_session(settings, auto=True)
    return persist.RecordedGame(session, persist.setup_for(session, seats, settings))


def run(game, limit=10_000):
    """Steps until the game stops (waiting or over); returns every message the steps broadcast."""
    out = []
    for _ in range(limit):
        msgs = game.step()
        if msgs is None:
            break
        out += msgs
    return out


def human_of(game):
    return next(c for c, f in game.engine.game_state.factions.items() if f.mode.name == 'HUMAN')


ONE_HUMAN = {'seats': [seat('HUMAN'), bot(), bot(), seat('NONCOMBATANT'), seat('NONCOMBATANT'), seat('NONCOMBATANT')]}


class TestRunningItself(unittest.TestCase):
    def test_an_all_bot_game_plays_exactly_as_one_stepped_with_next(self):
        settings = {'seats': [bot() for _ in range(6)], 'seed': 31}
        auto = auto_game(settings)
        manual, _ = build_session(settings)
        manual.handle_message({'type': 'watch'})
        for _ in range(150):
            auto.step()
            manual.handle_message({'type': 'next'})
        self.assertEqual(fingerprint(auto), fingerprint(manual))

    def test_it_stops_for_a_humans_phase_and_says_so(self):
        game = auto_game(dict(ONE_HUMAN, seed=32))
        msgs = run(game)
        human = human_of(game)
        self.assertEqual(game.waiting_for(), [human])
        self.assertIn(game.stepper._queue['phase'], ('PURCHASE', 'COMBAT_MOVE', 'NONCOMBAT_MOVE', 'DIPLOMACY'))
        self.assertEqual(msgs[-1]['type'], 'waiting')
        self.assertEqual(msgs[-1]['for'], [human])
        self.assertIsNone(game.step())  # still waiting

    def test_a_human_finishes_a_phase_with_end_phase_and_the_game_goes_on(self):
        game = auto_game(dict(ONE_HUMAN, seed=33))
        run(game)
        human = human_of(game)
        phase = game.stepper._queue['phase']
        [error] = game.handle_message({'type': 'next'})
        self.assertTrue(error['to_sender'])
        others = [c for c in game.engine.game_state.active_factions() if c != human]
        [error] = game.handle_message({'type': 'end_phase', 'faction': others[0]})
        self.assertEqual((error['type'], error['to_sender']), ('error', True))
        msgs = game.handle_message({'type': 'end_phase', 'faction': human})
        self.assertEqual(msgs[0]['type'], 'phase_result')
        self.assertEqual(msgs[0]['phase'], phase)
        run(game)
        self.assertEqual(game.waiting_for(), [human])  # its next decision, after whatever ran in between

    def test_a_whole_game_with_a_human_who_only_ends_phases(self):
        game = auto_game(dict(ONE_HUMAN, seed=34, armistice_rounds=0))
        human = human_of(game)
        for _ in range(200):
            run(game)
            if game.engine.game_state.game_over:
                break
            game.handle_message({'type': 'end_phase', 'faction': human})
        self.assertGreater(game.engine.game_state.round_number, 3)

    def test_an_armistice_proposal_waits_for_the_humans_answer(self):
        game = auto_game(dict(ONE_HUMAN, seed=35, armistice_rounds=1))
        human = human_of(game)
        for _ in range(100):
            run(game)
            if game._armistice is not None:
                break
            game.handle_message({'type': 'end_phase', 'faction': human})
        self.assertEqual(game.waiting_for(), [human])
        [error] = game.handle_message({'type': 'end_phase', 'faction': human})  # answer first
        self.assertEqual(error['type'], 'error')
        game.handle_message({'type': 'respond_armistice', 'faction': human, 'accept': False})
        self.assertIsNone(game._armistice)
        self.assertFalse(game.engine.game_state.game_over)

    def test_a_bots_invitation_to_a_human_waits_for_the_answer(self):
        game = auto_game(dict(ONE_HUMAN, seed=36))
        run(game)
        human = human_of(game)
        game.stepper._invitation = {'from': 'XX', 'to': human, 'answered': False}
        self.assertEqual(game.waiting_for(), [human])
        game.stepper._invitation['answered'] = True
        self.assertEqual(game.waiting_for(), [human])  # (back to its own phase)


class TestTheFeed(unittest.TestCase):
    def test_broadcasts_are_numbered_from_one_in_order_with_the_epoch(self):
        game = auto_game({'seats': [bot() for _ in range(6)], 'seed': 41})
        msgs = [m for _ in range(20) for m in game.step()]
        self.assertEqual([m['seq'] for m in msgs], list(range(1, len(msgs) + 1)))
        self.assertEqual({m['epoch'] for m in msgs}, {game.epoch})

    def test_a_follower_gets_what_it_missed(self):
        game = auto_game({'seats': [bot() for _ in range(6)], 'seed': 42})
        msgs = [m for _ in range(10) for m in game.step()]
        [feed] = game.handle_message({'type': 'follow', 'since': 5, 'epoch': game.epoch})
        self.assertTrue(feed['to_sender'])
        self.assertEqual([m['seq'] for m in feed['messages']], [m['seq'] for m in msgs if m['seq'] > 5])
        self.assertEqual(feed['seq'], game.seq)
        self.assertEqual(feed['state']['type'], 'state')
        self.assertEqual(feed['queue'], game.stepper._queue)
        [feed] = game.handle_message({'type': 'follow', 'since': game.seq, 'epoch': game.epoch})
        self.assertEqual(feed['messages'], [])

    def test_a_follower_too_far_behind_or_from_another_epoch_starts_from_the_state(self):
        game = auto_game({'seats': [bot() for _ in range(6)], 'seed': 43})
        with mock.patch.object(game.session, 'feed', game.session.feed.__class__(maxlen=5)):
            for _ in range(10):
                game.step()
            [feed] = game.handle_message({'type': 'follow', 'since': 1, 'epoch': game.epoch})
            self.assertIsNone(feed['messages'])
        [feed] = game.handle_message({'type': 'follow', 'since': 1, 'epoch': 'old'})
        self.assertIsNone(feed['messages'])
        [feed] = game.handle_message({'type': 'watch'})
        self.assertEqual(feed['type'], 'feed')
        self.assertIsNone(feed['messages'])

    def test_errors_go_to_the_sender_and_are_not_numbered(self):
        game = auto_game({'seats': [bot() for _ in range(6)], 'seed': 44})
        [error] = game.handle_message({'type': 'surrender', 'faction': 'NAA'})  # not a human seat
        self.assertTrue(error['to_sender'])
        self.assertNotIn('seq', error)
        self.assertEqual(game.seq, 0)


class TestSavingAnAutoGame(unittest.TestCase):
    def play(self, settings, steps, reload_at=()):
        game = auto_game(settings)
        human = human_of(game)
        for i in range(steps):
            if i in reload_at:
                game = persist.load(json.loads(json.dumps(game.doc())))
            if game.engine.game_state.game_over:
                break
            if game.step() is not None:
                continue
            queue = game.stepper._queue
            if queue['phase'] == 'PURCHASE' and not queue['human']['orders']:
                land = [tid for tid, t in sorted(queue['human']['targets'].items())
                        if t['remaining'] > 0 and game.engine.data.territories()[tid]['type'] == 'land']
                game.handle_message({'type': 'stage_purchase', 'faction': human,
                                     'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': land[0]}]})
            else:
                game.handle_message({'type': 'end_phase', 'faction': human})
        return game

    def test_reloading_mid_run_changes_nothing(self):
        settings = dict(ONE_HUMAN, seed=51)
        settings['seats'] = [seat('HUMAN'), bot('strategy'), bot(), seat('NONCOMBATANT'), seat('NONCOMBATANT'),
                             seat('NONCOMBATANT')]
        straight = self.play(settings, 160)
        reloaded = self.play(settings, 160, reload_at=range(3, 160, 6))
        self.assertEqual(fingerprint(reloaded), fingerprint(straight))
        self.assertGreater(straight.snapshots_taken, 3)

    def test_the_save_remembers_auto_mode(self):
        game = auto_game(dict(ONE_HUMAN, seed=52))
        run(game)
        again = persist.load(json.loads(json.dumps(game.doc())))
        self.assertTrue(again.auto)
        self.assertEqual(again.waiting_for(), game.waiting_for())
        self.assertNotEqual(again.epoch, game.epoch)  # (a reload numbers afresh: followers resync)


if __name__ == '__main__':
    unittest.main()
