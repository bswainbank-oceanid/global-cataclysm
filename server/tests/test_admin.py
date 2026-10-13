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
        self.assertEqual(admin.stats(self.store), {'players': 2, 'locked': 0, 'games_played': 2, 'games_completed': 1,
                                                   'games_live': 1, 'max_users': 100})
        self.assertTrue(live)


class TestMaxUsers(AdminTest):
    def test_past_the_most_players_new_accounts_are_made_locked(self):
        admin.set_max_users(self.store, 3)
        cat, _ = accounts.register(self.store, 'Cat', '', 'cat@x.com', 'pw')
        self.assertFalse(cat.get('locked'))
        dan, _ = accounts.register(self.store, 'Dan', '', 'dan@x.com', 'pw')  # (let in, but locked)
        self.assertTrue(dan['locked'])
        self.assertEqual((dan['lock']['reason'], dan['lock']['by']), (accounts.LOCK_USER_LIMIT, None))
        self.assertIn('all the players it takes', accounts.locked_note(dan))
        accounts.login(self.store, 'dan@x.com', 'pw')
        accounts.login(self.store, 'ann@x.com', 'pw')  # (players already here are unaffected)
        self.assertEqual((admin.stats(self.store)['players'], admin.stats(self.store)['locked']), (3, 1))
        admin.set_locked(self.store, cat['id'], True, by='Ann')  # a locked account isn't counted: room again
        eve, _ = accounts.register(self.store, 'Eve', '', 'eve@x.com', 'pw')
        self.assertFalse(eve.get('locked'))

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
    def test_a_locked_account_still_logs_in_and_says_who_locked_it_and_when(self):
        _, token = accounts.login(self.store, 'bob@x.com', 'pw')
        admin.set_locked(self.store, self.bob, True, by='Ann')
        user = accounts.login_with_token(self.store, token)  # (its logins don't end)
        self.assertTrue(user['locked'])
        user, _ = accounts.login(self.store, 'bob@x.com', 'pw')
        self.assertEqual((user['lock']['reason'], user['lock']['by']), (accounts.LOCK_ADMIN, 'Ann'))
        self.assertTrue(user['lock']['at'])
        self.assertIn('an admin locked it', accounts.locked_note(user))
        self.assertIn(accounts.DISCORD, accounts.locked_note(user))
        [bob] = admin.find_users(self.store, 'bob')
        self.assertTrue(bob['locked'] and bob['lock']['by'] == 'Ann')
        admin.set_locked(self.store, self.bob, False)
        [bob] = admin.find_users(self.store, 'bob')
        self.assertFalse(bob['locked'])
        self.assertIsNone(bob['lock'])
        self.assertNotIn('lock', accounts.get_user(self.store, self.bob))


class TestUpgrading(unittest.TestCase):
    def test_a_database_from_the_previous_version_is_brought_up_to_date(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'old.sqlite3')
            store = Store(path)
            store.insert('users', {'id': 'U_000001'}, id='U_000001', email='a@x', player_name='A')
            store._db.execute('DROP TABLE server_settings')  # (what a version 1 database looks like)
            store._db.execute('DROP TABLE shared_scenarios')
            store._db.execute('DROP TABLE user_preferences')
            store._db.execute('PRAGMA user_version = 1')
            store.close()
            store = Store(path)
            self.assertEqual(store._db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(store.fetch_all('shared_scenarios'), [])
            self.assertEqual(store.fetch_all('user_preferences'), [])
            admin.set_max_users(store, 50)
            self.assertEqual(admin.max_users(store), 50)
            self.assertEqual(store.fetch('users', id='U_000001'), {'id': 'U_000001'})
            store.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
