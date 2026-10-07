"""Limits on password guessing and account making (server/limits.py), and the hub applying them."""
import unittest
from unittest import mock

from server import accounts
from server.hub import Hub
from server.limits import Limits
from server.store import Store, StoreError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestLimits(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.limits = Limits({'login': (3, 600), 'login_account': (2, 600)}, clock=self.clock)

    def test_an_address_is_stopped_after_its_attempts_then_let_in_again(self):
        for _ in range(3):
            self.limits.check('login', '1.2.3.4')
            self.limits.failed('login', '1.2.3.4')
        with self.assertRaises(StoreError) as e:
            self.limits.check('login', '1.2.3.4')
        self.assertEqual(e.exception.problems, ['too many attempts: try again in 10 minutes'])
        self.limits.check('login', '5.6.7.8')  # (another address is unaffected)
        self.clock.now += 601
        self.limits.check('login', '1.2.3.4')

    def test_an_account_is_protected_from_many_addresses(self):
        for address in ('1.1.1.1', '2.2.2.2'):
            self.limits.failed('login', address, 'Ann@x.com')
        with self.assertRaises(StoreError):
            self.limits.check('login', '3.3.3.3', 'ann@X.com ')
        self.limits.check('login', '3.3.3.3', 'bob@x.com')

    def test_no_address_no_limit_by_address(self):
        for _ in range(10):
            self.limits.failed('login', None)
        self.limits.check('login', None)


class TestHubLimits(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.store = Store(':memory:')
        self.hub = Hub(self.store, download_url='https://example.com/gc', limits=Limits(
            {'login': (3, 600), 'login_account': (100, 600), 'register': (2, 3600)}))

    def tearDown(self):
        self.store.close()

    def reply(self, key, msg):
        [(_, m)] = self.hub.handle(key, msg)
        return m

    def test_hello_says_where_to_get_the_client(self):
        [(_, hello)] = self.hub.connect('c1', '9.9.9.9')
        self.assertEqual(hello['download_url'], 'https://example.com/gc')

    def test_wrong_passwords_from_one_address_are_stopped(self):
        self.hub.connect('c1', '9.9.9.9')
        self.reply('c1', {'type': 'register', 'player_name': 'Ann', 'email': 'ann@x.com', 'password': 'pw'})
        for _ in range(3):
            m = self.reply('c1', {'type': 'login', 'email': 'ann@x.com', 'password': 'wrong'})
            self.assertEqual(m['message'], 'wrong email or password')
        m = self.reply('c1', {'type': 'login', 'email': 'ann@x.com', 'password': 'pw'})
        self.assertEqual((m['type'], m['message']), ('login_failed', 'too many attempts: try again in 10 minutes'))
        self.hub.connect('c2', '8.8.8.8')
        self.assertEqual(self.reply('c2', {'type': 'login', 'email': 'ann@x.com', 'password': 'pw'})['type'], 'logged_in')

    def test_accounts_from_one_address_are_limited(self):
        self.hub.connect('c1', '9.9.9.9')
        for name in ('Ann', 'Bob'):
            m = self.reply('c1', {'type': 'register', 'player_name': name, 'email': f'{name}@x.com', 'password': 'pw'})
            self.assertEqual(m['type'], 'logged_in')
        m = self.reply('c1', {'type': 'register', 'player_name': 'Cat', 'email': 'cat@x.com', 'password': 'pw'})
        self.assertEqual(m['message'], 'too many attempts: try again in 60 minutes')


if __name__ == '__main__':
    unittest.main()
