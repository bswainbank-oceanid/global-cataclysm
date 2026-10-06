"""The Admin panel's server side (server/admin.py) and what it changes for accounts (server/accounts.py)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import accounts, admin, games
from server.store import Store, StoreError
from server.tests.test_games import DUEL, humans


class AdminTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.store = Store(':memory:')
        self.ann = accounts.register(self.store, 'Ann', 'Ann Smith', 'ann@x.com', 'pw')[0]['id']
        self.bob = accounts.register(self.store, 'Bob', '', 'bob@x.com', 'pw')[0]['id']

    def tearDown(self):
        self.store.close()


class TestStats(AdminTest):
    def test_games_played_counts_started_games_not_lobbies(self):
        live = games.create(self.store, self.ann, DUEL, humans(1))['id']
        done = games.create(self.store, self.ann, DUEL, humans(1))['id']
        games.finish(self.store, done)
        games.create(self.store, self.ann, DUEL, humans(2))  # a lobby, still forming
        cancelled = games.create(self.store, self.ann, DUEL, humans(2))['id']
        games.cancel(self.store, cancelled, self.ann)
        self.assertEqual(admin.stats(self.store), {'players': 2, 'games_played': 2, 'games_completed': 1,
                                                   'games_live': 1, 'max_users': 100})
        self.assertTrue(live)


class TestMaxUsers(AdminTest):
    def test_registration_stops_at_the_most_players(self):
        admin.set_max_users(self.store, 3)
        accounts.register(self.store, 'Cat', '', 'cat@x.com', 'pw')
        with self.assertRaises(StoreError) as e:
            accounts.register(self.store, 'Dan', '', 'dan@x.com', 'pw')
        self.assertEqual(e.exception.problems, ['this server has room for 3 players, and it is full'])
        accounts.login(self.store, 'ann@x.com', 'pw')  # (players already here are unaffected)

    def test_the_most_players_is_a_sensible_number(self):
        for bad in [0, -5, 'ten', 2.5, True]:
            with self.assertRaises(StoreError):
                admin.set_max_users(self.store, bad)
        self.assertEqual(admin.max_users(self.store), 100)


class TestLookup(AdminTest):
    def test_players_are_found_by_name_actual_name_or_email(self):
        self.assertEqual([u['player_name'] for u in admin.find_users(self.store, 'SMITH')], ['Ann'])
        self.assertEqual([u['player_name'] for u in admin.find_users(self.store, 'bob@')], ['Bob'])
        self.assertEqual([u['player_name'] for u in admin.find_users(self.store, '')], ['Ann', 'Bob'])
        self.assertNotIn('password', admin.find_users(self.store, 'ann')[0])

    def test_a_player_shows_their_games_and_last_login(self):
        games.create(self.store, self.bob, DUEL, humans(1))
        accounts.login(self.store, 'bob@x.com', 'pw')
        [bob] = admin.find_users(self.store, 'bob')
        self.assertEqual(bob['games'], {'forming': 0, 'live': 1, 'finished': 0})
        self.assertIsNotNone(bob['last_login'])


class TestLocking(AdminTest):
    def test_a_locked_account_cant_log_in_and_its_logins_end(self):
        _, token = accounts.login(self.store, 'bob@x.com', 'pw')
        admin.set_locked(self.store, self.bob, True)
        self.assertIsNone(accounts.login_with_token(self.store, token))
        with self.assertRaises(StoreError) as e:
            accounts.login(self.store, 'bob@x.com', 'pw')
        self.assertEqual(e.exception.problems, ['this account is locked: ask an admin'])
        self.assertTrue(admin.find_users(self.store, 'bob')[0]['locked'])
        admin.set_locked(self.store, self.bob, False)
        accounts.login(self.store, 'bob@x.com', 'pw')
        self.assertTrue(self.store.fetch_all('logins', user_id=self.bob))


class TestUpgrading(unittest.TestCase):
    def test_a_database_from_the_previous_version_is_brought_up_to_date(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'old.sqlite3')
            store = Store(path)
            store.insert('users', {'id': 'U_000001'}, id='U_000001', email='a@x', player_name='A')
            store._db.execute('DROP TABLE server_settings')  # (what a version 1 database looks like)
            store._db.execute('PRAGMA user_version = 1')
            store.close()
            store = Store(path)
            self.assertEqual(store._db.execute('PRAGMA user_version').fetchone()[0], 2)
            admin.set_max_users(store, 50)
            self.assertEqual(admin.max_users(store), 50)
            self.assertEqual(store.fetch('users', id='U_000001'), {'id': 'U_000001'})
            store.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
