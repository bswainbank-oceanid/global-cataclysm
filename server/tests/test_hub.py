"""The multi-player server's logic (server/hub.py): logging in, many games at once, routing by game and
player, saving as games run, and loading them back after a restart."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import accounts, games
from server.hub import Hub
from server.store import Store
from server.tests.test_persist import bot, fingerprint, seat


def settings(*seats, **kw):
    seats = list(seats) + [seat('NONCOMBATANT')] * (6 - len(seats))
    return {'seats': seats, **kw}


ONE_HUMAN = settings(seat('HUMAN'), bot(), bot(), seed=61)
TWO_HUMANS = settings(seat('HUMAN'), seat('HUMAN'), bot(), seed=62)
SCENARIO = {'kind': 'fixed', 'id': 'fixed', 'name': None}


class HubTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'players.sqlite3')
        self.store = Store(self.path)
        self.hub = Hub(self.store)
        self.ann, self.ann_token = self.user('ann', 'Ann')
        self.bob, self.bob_token = self.user('bob', 'Bob')

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def user(self, key, name):
        self.hub.connect(key)
        [(_, msg)] = self.hub.handle(key, {'type': 'register', 'player_name': name, 'actual_name': '',
                                           'email': f'{key}@x.com', 'password': 'pw'})
        return msg['user']['id'], msg['token']

    def send(self, key, msg):
        return self.hub.handle(key, msg)

    def to(self, deliveries, key):
        """The messages delivered to connection `key`."""
        return [m for keys, m in deliveries if key in keys]

    def one_human_game(self, host=None, s=ONE_HUMAN):
        game = games.create(self.store, host or self.ann, SCENARIO, s)
        self.hub.start_game(game['id'])
        return game['id']

    def run_game(self, game_id, limit=5000):
        out = []
        for _ in range(limit):
            if game_id not in self.hub.runnable():
                break
            out += self.hub.step(game_id)
        return out

    def faction_of(self, game_id, user):
        return sorted(self.hub._factions_of(games.get(self.store, game_id), user))[0]


class TestLoggingIn(HubTest):
    def test_a_new_connection_is_greeted_and_must_log_in(self):
        self.assertEqual(self.hub.connect('c1'), [(['c1'], {'type': 'hello', 'logged_in': False})])
        [(keys, error)] = self.send('c1', {'type': 'enter_game', 'game_id': 'G_000001'})
        self.assertEqual((keys, error['type'], error['message']), (['c1'], 'error', 'log in first'))

    def test_a_saved_token_logs_another_connection_in_until_logout(self):
        self.hub.connect('c2')
        [(_, msg)] = self.send('c2', {'type': 'token_login', 'token': self.ann_token})
        self.assertEqual((msg['type'], msg['user']['player_name']), ('logged_in', 'Ann'))
        self.send('ann', {'type': 'logout'})
        self.hub.connect('c3')
        [(_, msg)] = self.send('c3', {'type': 'token_login', 'token': self.ann_token})
        self.assertEqual(msg['type'], 'error')

    def test_refusals_carry_their_problems(self):
        self.hub.connect('c4')
        [(_, msg)] = self.send('c4', {'type': 'register', 'player_name': 'ann', 'email': 'new@x.com', 'password': ''})
        self.assertEqual(msg['problems'], ['a password needs at least one character'])
        [(_, msg)] = self.send('c4', {'type': 'login', 'email': 'ann@x.com', 'password': 'nope'})
        self.assertEqual(msg['problems'], ['wrong email or password'])


class TestRunningGames(HubTest):
    def test_a_started_game_runs_until_it_needs_its_human_and_records_that(self):
        gid = self.one_human_game()
        self.assertIn(gid, self.hub.runnable())
        self.run_game(gid)
        self.assertNotIn(gid, self.hub.runnable())
        record = games.get(self.store, gid)
        self.assertEqual(record['progress']['waiting_for'], [self.ann])
        self.assertEqual(len(record['factions']), 6)
        self.assertIsNotNone(games.load_state(self.store, gid))

    def test_only_a_player_in_the_game_can_enter_it(self):
        gid = self.one_human_game()
        [(_, msg)] = self.send('bob', {'type': 'enter_game', 'game_id': gid})
        self.assertEqual(msg['message'], 'you are not playing in that game')
        out = self.send('ann', {'type': 'enter_game', 'game_id': gid})
        entered, feed = [m for _, m in out]
        self.assertEqual(entered['type'], 'entered_game')
        self.assertEqual(entered['my_factions'], [self.faction_of(gid, self.ann)])
        self.assertEqual(feed['type'], 'feed')
        self.assertEqual({k for keys, _ in out for k in keys}, {'ann'})

    def test_a_game_broadcasts_to_its_own_connections_only(self):
        gid = self.one_human_game()
        bobs = self.one_human_game(host=self.bob)
        self.send('ann', {'type': 'enter_game', 'game_id': gid})
        self.hub.connect('ann2')
        self.send('ann2', {'type': 'token_login', 'token': self.ann_token})
        self.send('ann2', {'type': 'enter_game', 'game_id': gid})
        self.send('bob', {'type': 'enter_game', 'game_id': bobs})
        out = self.run_game(gid)
        self.assertTrue(out)
        for keys, _ in out:
            self.assertEqual(sorted(keys), ['ann', 'ann2'])
        self.assertTrue(all(keys == ['bob'] for keys, _ in self.run_game(bobs)))

    def test_a_player_acts_only_for_their_own_factions(self):
        gid = self.one_human_game()
        self.run_game(gid)
        self.send('ann', {'type': 'enter_game', 'game_id': gid})
        mine = self.faction_of(gid, self.ann)
        other = next(f for f in games.get(self.store, gid)['factions'].values() if f != mine)
        [(_, msg)] = self.send('ann', {'type': 'end_phase', 'faction': other})
        self.assertEqual(msg['message'], f'you are not playing {other}')
        [(_, msg)] = self.send('ann', {'type': 'end_phase'})
        self.assertEqual(msg['message'], 'say which of your factions this is for')
        out = self.send('ann', {'type': 'end_phase', 'faction': mine})
        self.assertEqual(self.to(out, 'ann')[0]['type'], 'phase_result')

    def test_seats_are_matched_to_their_players_in_a_launched_game(self):
        game = games.create(self.store, self.ann, SCENARIO, TWO_HUMANS)
        games.take_seat(self.store, game['id'], self.ann, 1)
        games.take_seat(self.store, game['id'], self.bob, 2)
        games.launch(self.store, game['id'], self.ann)
        self.hub.start_game(game['id'])
        gid = game['id']
        self.send('ann', {'type': 'enter_game', 'game_id': gid})
        self.send('bob', {'type': 'enter_game', 'game_id': gid})
        ann_f, bob_f = self.faction_of(gid, self.ann), self.faction_of(gid, self.bob)
        self.assertNotEqual(ann_f, bob_f)
        self.run_game(gid)
        waiting = self.hub.sessions[gid].waiting_for()
        self.assertEqual(len(waiting), 1)
        who = self.ann if waiting == [ann_f] else self.bob
        self.assertEqual(games.get(self.store, gid)['progress']['waiting_for'], [who])
        [(_, msg)] = self.send('bob' if who == self.ann else 'ann', {'type': 'end_phase', 'faction': waiting[0]})
        self.assertEqual(msg['type'], 'error')  # not theirs

    def test_a_game_that_ends_is_marked_finished(self):
        gid = self.one_human_game(s=settings(bot(), bot(), seed=63, armistice_rounds=1))  # (all bots accept)
        self.run_game(gid)
        self.assertTrue(self.hub.sessions[gid].engine.game_state.game_over)
        self.assertEqual(games.get(self.store, gid)['status'], 'finished')
        self.assertEqual(self.hub.runnable(), [])


class TestRestarting(HubTest):
    def test_live_games_come_back_where_they_were(self):
        gid = self.one_human_game()
        self.run_game(gid)
        mine = self.faction_of(gid, self.ann)
        self.send('ann', {'type': 'enter_game', 'game_id': gid})
        self.send('ann', {'type': 'end_phase', 'faction': mine})
        self.run_game(gid)
        before = fingerprint(self.hub.sessions[gid])
        self.store.close()
        self.store = Store(self.path)
        hub = Hub(self.store)
        self.assertEqual(fingerprint(hub.sessions[gid]), before)
        self.assertEqual(hub.sessions[gid].waiting_for(), [mine])

    def test_a_restart_mid_run_finishes_the_run_the_same_way(self):
        gid = self.one_human_game(s=settings(seat('HUMAN'), bot('strategy'), bot(), seed=64))
        twin = self.one_human_game(s=settings(seat('HUMAN'), bot('strategy'), bot(), seed=64))
        self.run_game(twin)
        for _ in range(7):  # part way through the bots' turns
            self.hub.step(gid)
        self.store.close()
        self.store = Store(self.path)
        self.hub = Hub(self.store)
        self.run_game(gid)
        self.run_game(twin)
        self.assertEqual(fingerprint(self.hub.sessions[gid]), fingerprint(self.hub.sessions[twin]))

    def test_an_unreadable_save_is_reported_not_fatal(self):
        gid = self.one_human_game()
        save = games.load_state(self.store, gid)
        games.save_state(self.store, gid, dict(save['session'], version=999))
        hub = Hub(self.store)
        self.assertNotIn(gid, hub.sessions)
        self.assertIn(gid, hub.load_problems)
        hub.connect('ann')
        hub.handle('ann', {'type': 'token_login', 'token': self.ann_token})
        [(_, msg)] = hub.handle('ann', {'type': 'enter_game', 'game_id': gid})
        self.assertIn("can't be played right now", msg['message'])


if __name__ == '__main__':
    unittest.main()
