"""A game's playback kept for good (server/feed_log.py): a player resumes from where they left off -- across a
server restart, and after the game has ended without them; a finished game stays in their Resume list (My Games)
until they've watched it to the end."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import accounts, feed_log, games
from server.hub import Hub
from server.store import Store
from server.tests.test_persist import bot, seat

ALL_BOTS = {'seats': [bot('strategy'), bot('strategy')] + [seat('NONCOMBATANT')] * 4, 'seed': 81}


class TestWatchToTheEnd(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'players.sqlite3')
        self.store = Store(self.path)
        self.hub = Hub(self.store)
        self.hub.connect('ann')
        [(_, msg)] = self.hub.handle('ann', {'type': 'register', 'player_name': 'Ann', 'email': 'ann@x.com', 'password': 'pw'})
        self.ann = msg['user']['id']

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def play(self, game_id, unpaced=False, limit=5000):
        """Steps the game while it can go; `unpaced`: as if someone else kept watching (an all-bot game keeps pace
        with its viewers), so it runs on to the end without Ann."""
        for _ in range(limit):
            session = self.hub.sessions[game_id].session  # (the hub keeps it inside a RecordedGame)
            if unpaced:
                session.viewer_seq = session.seq
            if game_id not in self.hub.runnable():
                break
            self.hub.step(game_id)

    def my_games(self):
        [(_, reply)] = self.hub.handle('ann', {'type': 'my_games'})
        return {g['id']: g for g in reply['games']}

    def test_a_game_that_ended_while_away_is_resumed_and_watched_to_its_end(self):
        out = self.hub.handle('ann', {'type': 'create_game', 'scenario': {'kind': 'fixed', 'id': 'fixed'}, 'settings': ALL_BOTS})
        game_id = next(m['game']['id'] for _, m in out if m['type'] == 'entered_game')
        self.play(game_id)  # (a little way ahead of Ann, who is watching)
        # Ann plays back to the 5th phase result she has, then leaves.
        rows =self.store.query("SELECT epoch, seq FROM game_feed WHERE game_id = ? AND kind = 'phase_result' ORDER BY rowid",
                                (game_id,))
        self.assertGreater(len(rows), 5)
        seen = rows[4]
        self.hub.handle('ann', {'type': 'played', 'seq': seen['seq'], 'epoch': seen['epoch']})
        self.hub.handle('ann', {'type': 'leave_game'})
        # The game goes on to its end without her.
        self.play(game_id, unpaced=True)
        self.assertEqual(games.get(self.store, game_id)['status'], games.FINISHED)
        mine = self.my_games()
        self.assertIn(game_id, mine)
        self.assertEqual(mine[game_id]['status'], 'finished')
        # A restart of the server doesn't lose it.
        self.hub = Hub(self.store)
        self.hub.connect('ann')
        [(_, msg)] = self.hub.handle('ann', {'type': 'login', 'email': 'ann@x.com', 'password': 'pw'})
        self.assertIn(game_id, self.my_games())
        # Entering it: from the point she left, everything since -- the game's end last.
        out = self.hub.handle('ann', {'type': 'enter_game', 'game_id': game_id})
        [feed] = [m for _, m in out if m['type'] == 'feed']
        self.assertEqual(feed['resume'], {'epoch': seen['epoch'], 'seq': seen['seq']})
        self.assertIsNone(feed['game_over'])
        played = feed['messages']
        self.assertEqual((played[0]['epoch'], played[0]['seq']), (seen['epoch'], seen['seq'] + 1))
        self.assertIn('game_over', [m['type'] for m in played])
        self.assertEqual(feed['state']['type'], 'state')
        # Watched to the end: it leaves her list.
        last = [m for m in played if m['type'] == 'phase_result'][-1]
        self.hub.handle('ann', {'type': 'played', 'seq': last['seq'], 'epoch': last['epoch']})
        self.assertNotIn(game_id, self.my_games())

    def test_a_finished_game_she_watched_to_the_end_is_not_listed(self):
        out = self.hub.handle('ann', {'type': 'create_game', 'scenario': {'kind': 'fixed', 'id': 'fixed'}, 'settings': ALL_BOTS})
        game_id = next(m['game']['id'] for _, m in out if m['type'] == 'entered_game')
        self.play(game_id, unpaced=True)
        last = feed_log.last_result(self.store, game_id)
        self.hub.handle('ann', {'type': 'played', 'seq': last[1], 'epoch': last[0]})
        self.assertNotIn(game_id, self.my_games())


if __name__ == '__main__':
    unittest.main()
