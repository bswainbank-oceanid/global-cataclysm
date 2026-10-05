"""Player accounts and logins (server/accounts.py)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import accounts
from server.store import Store, StoreError

def fast(test):
    """Hashes with few iterations for the rest of `test` (the real count is slow on purpose)."""
    patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
    patch.start()
    test.addCleanup(patch.stop)


class AccountTest(unittest.TestCase):
    def setUp(self):
        fast(self)
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.close()

    def ann(self, **kw):
        details = {'player_name': 'Ann', 'actual_name': 'Ann Smith', 'email': 'Ann@Example.com', 'password': 'pw', **kw}
        return accounts.register(self.store, **details)

    def problems(self, **kw):
        with self.assertRaises(StoreError) as e:
            self.ann(**kw)
        return e.exception.problems


class TestRegistering(AccountTest):
    def test_a_new_account_is_logged_in_and_has_no_password_in_it(self):
        user, token = self.ann()
        self.assertEqual(user['id'], 'U_000001')
        self.assertEqual((user['player_name'], user['actual_name'], user['email'], user['admin']),
                         ('Ann', 'Ann Smith', 'Ann@Example.com', False))
        self.assertNotIn('password', user)
        self.assertEqual(accounts.login_with_token(self.store, token)['id'], user['id'])

    def test_details_are_trimmed_and_the_actual_name_may_be_blank(self):
        user, _ = self.ann(player_name='  Ann ', actual_name='', email=' ann@example.com ')
        self.assertEqual((user['player_name'], user['actual_name'], user['email']), ('Ann', '', 'ann@example.com'))

    def test_the_email_and_player_name_must_be_unique_whatever_their_case(self):
        self.ann()
        self.assertEqual(self.problems(player_name='Bob', email='ann@EXAMPLE.com'), ['that email is already registered'])
        self.assertEqual(self.problems(player_name='aNN', email='bob@example.com'), ['that player name is already taken'])

    def test_a_refused_account_uses_up_nothing(self):
        self.ann()
        self.problems(player_name='ann', email='bob@example.com')
        user, _ = self.ann(player_name='Bob', email='bob@example.com')
        self.assertEqual(user['id'], 'U_000002')
        self.assertEqual(len(self.store.fetch_all('users')), 2)

    def test_a_password_needs_one_character_and_nothing_more(self):
        self.assertEqual(self.problems(password=''), ['a password needs at least one character'])
        self.ann(password=' ')  # anything at all

    def test_missing_or_malformed_details_are_all_reported(self):
        problems = self.problems(player_name=' ', email='not-an-email', password='')
        self.assertEqual(problems, ['a player name is needed', 'that is not an email address',
                                    'a password needs at least one character'])
        self.assertEqual(self.problems(player_name='x' * 25), ['a player name may be at most 24 characters'])
        for bad in ['a@', '@b', 'a b@c', 'a@b@c', '']:
            self.assertTrue(self.problems(email=bad), bad)


class TestPasswords(AccountTest):
    def test_only_a_salted_hash_is_stored(self):
        self.ann(password='hunter2')
        self.ann(player_name='Bob', email='bob@example.com', password='hunter2')
        stored = [u['password'] for u in self.store.fetch_all('users')]
        for p in stored:
            self.assertEqual(p['scheme'], 'pbkdf2_sha256')
            self.assertNotIn('hunter2', str(p))
        self.assertNotEqual(stored[0]['salt'], stored[1]['salt'])
        self.assertNotEqual(stored[0]['hash'], stored[1]['hash'])  # (same password, different salt)

    def test_the_right_password_logs_in_with_the_email_in_any_case(self):
        self.ann(password='Secret!')
        user, token = accounts.login(self.store, ' ann@example.COM ', 'Secret!')
        self.assertEqual(user['player_name'], 'Ann')
        self.assertNotIn('password', user)
        self.assertEqual(accounts.login_with_token(self.store, token)['player_name'], 'Ann')

    def test_a_wrong_password_or_unknown_email_fails_the_same_way(self):
        self.ann(password='Secret!')
        for email, password in [('ann@example.com', 'secret!'), ('ann@example.com', ''), ('nobody@example.com', 'Secret!')]:
            with self.assertRaises(StoreError) as e:
                accounts.login(self.store, email, password)
            self.assertEqual(e.exception.problems, ['wrong email or password'])

    def test_an_account_keeps_working_when_the_iteration_count_changes(self):
        self.ann(password='pw')
        with mock.patch.object(accounts, 'ITERATIONS', 2000):
            accounts.login(self.store, 'ann@example.com', 'pw')
            self.ann(player_name='Bob', email='bob@example.com')
        self.assertEqual([u['password']['iterations'] for u in self.store.fetch_all('users')], [1000, 2000])


class TestLogins(AccountTest):
    def test_only_a_hash_of_a_token_is_stored(self):
        _, token = self.ann()
        [row] = self.store.fetch_all('logins')
        self.assertNotIn(token, str(row))

    def test_logging_out_ends_that_login_only(self):
        _, first = self.ann()
        _, second = accounts.login(self.store, 'ann@example.com', 'pw')
        accounts.logout(self.store, first)
        self.assertIsNone(accounts.login_with_token(self.store, first))
        self.assertEqual(accounts.login_with_token(self.store, second)['player_name'], 'Ann')  # another client

    def test_a_made_up_or_empty_token_logs_no_one_in(self):
        self.ann()
        for token in ['made-up', '', None]:
            self.assertIsNone(accounts.login_with_token(self.store, token))
        accounts.logout(self.store, 'made-up')  # (harmless)

    def test_logging_back_in_notes_when(self):
        _, token = self.ann()
        [before] = self.store.fetch_all('logins')
        with mock.patch.object(accounts, 'utc_now', return_value='2030-01-01T00:00:00Z'):
            accounts.login_with_token(self.store, token)
        [after] = self.store.fetch_all('logins')
        self.assertEqual((after['created'], after['last_used']), (before['created'], '2030-01-01T00:00:00Z'))


class TestAdmin(AccountTest):
    def test_the_admin_flag_is_off_until_set(self):
        user, token = self.ann()
        self.assertFalse(accounts.is_admin(self.store, user['id']))
        accounts.set_admin(self.store, user['id'])
        self.assertTrue(accounts.is_admin(self.store, user['id']))
        self.assertTrue(accounts.login_with_token(self.store, token)['admin'])
        accounts.set_admin(self.store, user['id'], False)
        self.assertFalse(accounts.is_admin(self.store, user['id']))
        self.assertFalse(accounts.is_admin(self.store, 'U_000404'))
        with self.assertRaises(StoreError):
            accounts.set_admin(self.store, 'U_000404')

    def test_a_user_is_found_by_email_or_player_name(self):
        user, _ = self.ann()
        for who in ['ann@example.com', 'ANN', ' Ann ']:
            self.assertEqual(accounts.find_user(self.store, who)['id'], user['id'])
        self.assertIsNone(accounts.find_user(self.store, 'nobody'))


class TestSurvivesRestart(unittest.TestCase):
    def setUp(self):
        fast(self)

    def test_a_login_works_after_the_database_is_reopened(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'players.sqlite3')
            store = Store(path)
            _, token = accounts.register(store, 'Ann', '', 'ann@example.com', 'pw')
            store.close()
            store = Store(path)
            self.assertEqual(accounts.login_with_token(store, token)['player_name'], 'Ann')
            store.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
