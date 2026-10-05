"""Games and chat in the player database (server/games.py, server/chat.py)."""
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from server import accounts, chat, games
from server.store import Store, StoreError
from server.tests.test_lobby import seat, settings

DUEL = {'kind': 'shared', 'id': 'Setup_002', 'name': 'Duel'}


def humans(n, bots=1, faction=None):
    """Settings with `n` HUMAN seats (the first naming `faction` if given) and `bots` BOT seats."""
    seats = [seat('HUMAN', faction if i == 0 and faction else 'random') for i in range(n)] + [seat('BOT')] * bots
    return settings(*seats)


class GameTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.store = Store(':memory:')
        self.ann = accounts.register(self.store, 'Ann', '', 'ann@x.com', 'pw')[0]['id']
        self.bob = accounts.register(self.store, 'Bob', '', 'bob@x.com', 'pw')[0]['id']
        self.cat = accounts.register(self.store, 'Cat', '', 'cat@x.com', 'pw')[0]['id']

    def tearDown(self):
        self.store.close()

    def lobby(self, n=3, **kw):
        return games.create(self.store, self.ann, DUEL, humans(n, **kw))

    def refused(self, fn, *args):
        with self.assertRaises(StoreError) as e:
            fn(*args)
        return e.exception.problems


class TestCreating(GameTest):
    def test_two_or_more_humans_open_a_lobby_with_every_human_seat_open(self):
        game = self.lobby(3)
        self.assertEqual((game['id'], game['status'], game['host_id']), ('G_000001', 'forming', self.ann))
        self.assertEqual(games.open_seats(game), [1, 2, 3])
        self.assertIsNone(game['started'])
        self.assertEqual(games.get(self.store, game['id']), game)

    def test_one_human_starts_at_once_with_the_host_in_the_seat(self):
        game = games.create(self.store, self.ann, DUEL, humans(1))
        self.assertEqual(game['status'], 'live')
        self.assertEqual(game['seats'][0]['user_id'], self.ann)
        self.assertIsNotNone(game['started'])

    def test_all_bots_start_at_once_owned_by_the_host(self):
        game = games.create(self.store, self.ann, DUEL, humans(0, bots=2))
        self.assertEqual(game['status'], 'live')
        self.assertEqual([g['id'] for g in games.my_games(self.store, self.ann)], [game['id']])

    def test_a_game_needs_valid_settings_a_scenario_and_a_real_host(self):
        self.assertTrue(self.refused(games.create, self.store, self.ann, DUEL, settings(seat('HUMAN'))))  # one player
        self.assertTrue(self.refused(games.create, self.store, self.ann, {'kind': 'odd'}, humans(2)))
        self.assertTrue(self.refused(games.create, self.store, 'U_000404', DUEL, humans(2)))
        self.assertEqual(self.store.fetch_all('games'), [])

    def test_codes_are_six_unambiguous_characters_and_unique(self):
        codes = {self.lobby()['code'] for _ in range(30)}
        self.assertEqual(len(codes), 30)
        for code in codes:
            self.assertRegex(code, r'^[A-HJ-NP-Z2-9]{6}$')

    def test_a_code_already_in_use_is_drawn_again(self):
        first = games.create(self.store, self.ann, DUEL, humans(2), random.Random(1))
        second = games.create(self.store, self.ann, DUEL, humans(2), random.Random(1))  # same first draw
        self.assertNotEqual(first['code'], second['code'])

    def test_a_game_is_found_by_its_code_in_any_case(self):
        game = self.lobby()
        self.assertEqual(games.find_by_code(self.store, f" {game['code'].lower()} ")['id'], game['id'])
        self.assertIsNone(games.find_by_code(self.store, 'ZZZZZZ'))
        self.assertIsNone(games.find_by_code(self.store, ''))


class TestSeats(GameTest):
    def test_players_take_their_own_seats_the_host_too_and_one_may_take_several(self):
        gid = self.lobby(3)['id']
        games.take_seat(self.store, gid, self.bob, 1)
        games.take_seat(self.store, gid, self.ann, 2)
        game = games.take_seat(self.store, gid, self.ann, 3)
        self.assertEqual([s['user_id'] for s in game['seats'][:3]], [self.bob, self.ann, self.ann])
        games.take_seat(self.store, gid, self.ann, 3)  # (taking your own seat again is harmless)

    def test_a_taken_or_non_human_seat_is_refused(self):
        gid = self.lobby(2)['id']
        games.take_seat(self.store, gid, self.bob, 1)
        self.assertEqual(self.refused(games.take_seat, self.store, gid, self.cat, 1), ['seat 1 is taken'])
        self.assertEqual(self.refused(games.take_seat, self.store, gid, self.cat, 3), ['seat 3 is not for a human player'])
        self.assertEqual(self.refused(games.take_seat, self.store, gid, self.cat, 9), ['there is no seat 9'])

    def test_only_your_own_seat_can_be_given_back(self):
        gid = self.lobby(2)['id']
        games.take_seat(self.store, gid, self.bob, 1)
        self.assertEqual(self.refused(games.leave_seat, self.store, gid, self.cat, 1), ['seat 1 is not yours'])
        game = games.leave_seat(self.store, gid, self.bob, 1)
        self.assertEqual(games.open_seats(game), [1, 2])

    def test_seats_change_only_while_forming(self):
        gid = self.lobby(2)['id']
        games.cancel(self.store, gid, self.ann)
        self.assertEqual(self.refused(games.take_seat, self.store, gid, self.bob, 1), ['that game was cancelled'])


class TestLaunchAndCancel(GameTest):
    def test_the_host_launches_once_every_human_seat_is_taken(self):
        gid = self.lobby(2)['id']
        games.take_seat(self.store, gid, self.bob, 1)
        self.assertEqual(self.refused(games.launch, self.store, gid, self.ann),
                         ['every human seat must be taken first (open: 2)'])
        games.take_seat(self.store, gid, self.cat, 2)
        self.assertEqual(self.refused(games.launch, self.store, gid, self.bob), ['only the host can launch the game'])
        game = games.launch(self.store, gid, self.ann)  # (the host needn't sit)
        self.assertEqual(game['status'], 'live')
        self.assertIsNotNone(game['started'])
        self.assertEqual(self.refused(games.launch, self.store, gid, self.ann), ['that game has already started'])

    def test_only_the_host_cancels_and_only_a_forming_game(self):
        gid = self.lobby(2)['id']
        self.assertEqual(self.refused(games.cancel, self.store, gid, self.bob), ['only the host can cancel the game'])
        game = games.cancel(self.store, gid, self.ann)
        self.assertEqual((game['status'], bool(game['ended'])), ('cancelled', True))
        self.assertTrue(self.refused(games.cancel, self.store, gid, self.ann))

    def test_a_live_game_records_factions_progress_and_its_end(self):
        game = games.create(self.store, self.ann, DUEL, humans(1))
        games.record_factions(self.store, game['id'], {1: 'UER', 2: 'UE'})
        games.update_progress(self.store, game['id'], {'round': 3, 'turn': 2, 'active_faction': 'UE',
                                                       'waiting_for': []})
        game = games.finish(self.store, game['id'])
        self.assertEqual(game['factions'], {'1': 'UER', '2': 'UE'})
        self.assertEqual(game['progress']['round'], 3)
        self.assertEqual(game['status'], 'finished')
        self.assertTrue(self.refused(games.update_progress, self.store, game['id'], {}))  # over


class TestLists(GameTest):
    def test_available_games_are_forming_ones_with_an_open_seat(self):
        full = self.lobby(2)['id']
        open_one = self.lobby(3)['id']
        cancelled = self.lobby(2)['id']
        games.create(self.store, self.ann, DUEL, humans(1))  # live at once
        for n, who in [(1, self.ann), (2, self.bob)]:
            games.take_seat(self.store, full, who, n)
        games.take_seat(self.store, open_one, self.bob, 1)
        games.cancel(self.store, cancelled, self.ann)
        rows = games.open_games(self.store)
        self.assertEqual([r['id'] for r in rows], [open_one])
        row = rows[0]
        self.assertEqual((row['host_name'], row['humans'], row['open_seats'], row['scenario']['name']),
                         ('Ann', 3, 2, 'Duel'))

    def test_my_live_games_are_the_ones_i_host_or_sit_in_newest_first(self):
        hosted = games.create(self.store, self.ann, DUEL, humans(1))['id']
        lobby = self.lobby(2, faction='GPC')['id']
        games.take_seat(self.store, lobby, self.bob, 1)
        games.take_seat(self.store, lobby, self.cat, 2)
        games.launch(self.store, lobby, self.ann)
        bobs_own = games.create(self.store, self.bob, DUEL, humans(1))['id']
        self.assertEqual([g['id'] for g in games.my_games(self.store, self.ann)], [lobby, hosted])
        mine = games.my_games(self.store, self.bob)
        self.assertEqual([g['id'] for g in mine], [bobs_own, lobby])
        self.assertEqual((mine[1]['my_seats'], mine[1]['my_factions']), ([1], ['GPC']))  # named in the settings
        self.assertEqual(games.my_games(self.store, self.cat)[0]['my_factions'], [None])  # random: not dealt yet
        games.record_factions(self.store, lobby, {1: 'GPC', 2: 'AAC'})
        self.assertEqual(games.my_games(self.store, self.cat)[0]['my_factions'], ['AAC'])

    def test_my_games_can_ask_for_other_statuses(self):
        gid = self.lobby(2)['id']
        games.take_seat(self.store, gid, self.bob, 1)
        self.assertEqual(games.my_games(self.store, self.bob), [])
        self.assertEqual([g['id'] for g in games.my_games(self.store, self.bob, ('forming',))], [gid])

    def test_live_game_ids_are_what_a_restart_loads(self):
        live = games.create(self.store, self.ann, DUEL, humans(1))['id']
        self.lobby(2)
        self.assertEqual(games.live_game_ids(self.store), [live])


class TestSaves(GameTest):
    def test_a_live_games_state_saves_and_loads(self):
        gid = games.create(self.store, self.ann, DUEL, humans(1))['id']
        self.assertIsNone(games.load_state(self.store, gid))
        games.save_state(self.store, gid, {'phase': 'PURCHASE', 'n': 1})
        games.save_state(self.store, gid, {'phase': 'COMBAT_MOVE', 'n': 2})
        self.assertEqual(games.load_state(self.store, gid)['session'], {'phase': 'COMBAT_MOVE', 'n': 2})

    def test_only_a_live_game_saves(self):
        gid = self.lobby(2)['id']
        self.assertTrue(self.refused(games.save_state, self.store, gid, {}))

    def test_a_save_survives_reopening_the_database(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'p.sqlite3')
            store = Store(path)
            uid = accounts.register(store, 'Dan', '', 'dan@x.com', 'pw')[0]['id']
            gid = games.create(store, uid, DUEL, humans(1))['id']
            games.save_state(store, gid, {'x': 1})
            store.close()
            store = Store(path)
            self.assertEqual(games.live_game_ids(store), [gid])
            self.assertEqual(games.load_state(store, gid)['session'], {'x': 1})
            store.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestChat(GameTest):
    def test_messages_carry_the_player_name_and_come_back_oldest_first(self):
        chat.post(self.store, 'browse', self.ann, '  hello  ')
        chat.post(self.store, 'browse', self.bob, 'hi Ann')
        rows = chat.recent(self.store, 'browse')
        self.assertEqual([(m['player_name'], m['text']) for m in rows], [('Ann', 'hello'), ('Bob', 'hi Ann')])
        self.assertLess(rows[0]['id'], rows[1]['id'])

    def test_each_lobby_has_its_own_room(self):
        gid = self.lobby()['id']
        chat.post(self.store, gid, self.ann, 'lobby')
        chat.post(self.store, 'browse', self.ann, 'browse')
        self.assertEqual([m['text'] for m in chat.recent(self.store, gid)], ['lobby'])
        self.assertTrue(self.refused(chat.post, self.store, 'G_000404', self.ann, 'x'))

    def test_recent_keeps_the_last_ones(self):
        for i in range(8):
            chat.post(self.store, 'browse', self.ann, str(i))
        self.assertEqual([m['text'] for m in chat.recent(self.store, 'browse', limit=3)], ['5', '6', '7'])

    def test_empty_or_overlong_messages_are_refused(self):
        self.assertEqual(self.refused(chat.post, self.store, 'browse', self.ann, '   '), ['a chat message needs some text'])
        self.assertTrue(self.refused(chat.post, self.store, 'browse', self.ann, 'x' * 501))
        chat.post(self.store, 'browse', self.ann, 'x' * 500)


if __name__ == '__main__':
    unittest.main()
