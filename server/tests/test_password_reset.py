"""A forgotten password: a 6-digit code emailed (server/hub.py request_reset, server/accounts.py start_reset),
then a new password with it (reset_password / finish_reset)."""
import datetime
import re
import unittest
from unittest import mock

from server import accounts, admin
from server.hub import Hub
from server.limits import Limits
from server.mailer import Mailer
from server.store import Store, StoreError


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send_later(self, to, subject, text):
        self.sent.append((to, subject, text))

    def code(self):
        return re.search(r'\b(\d{6})\b', self.sent[-1][2]).group(1)


class ResetTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.store = Store(':memory:')
        self.mailer = FakeMailer()
        self.hub = Hub(self.store, mailer=self.mailer, limits=Limits({'login': (100, 600), 'login_account': (100, 600)}))
        self.hub.connect('ann', '1.1.1.1')
        [(_, m)] = self.hub.handle('ann', {'type': 'register', 'player_name': 'Ann', 'email': 'ann@x.com', 'password': 'old'})
        self.old_token = m['token']
        self.hub.connect('c', '2.2.2.2')

    def tearDown(self):
        self.store.close()

    def reply(self, msg, key='c'):
        [(_, m)] = self.hub.handle(key, msg)
        return m

    def ask(self, email='ann@x.com'):
        return self.reply({'type': 'request_reset', 'email': email})

    def reset(self, code, password='new pw', email='ann@x.com'):
        return self.reply({'type': 'reset_password', 'email': email, 'code': code, 'password': password})


class TestAsking(ResetTest):
    def test_a_code_is_emailed_to_the_account(self):
        self.assertEqual(self.ask(), {'type': 'reset_requested', 'email': 'ann@x.com'})
        [(to, subject, text)] = self.mailer.sent
        self.assertEqual(to, 'ann@x.com')
        self.assertIn('password reset code', subject)
        self.assertIn('Hello Ann', text)
        self.assertRegex(text, r'\b\d{6}\b')
        self.assertIn('30 minutes', text)

    def test_an_unknown_email_gets_the_same_answer_and_no_email(self):
        self.assertEqual(self.ask('nobody@x.com'), {'type': 'reset_requested', 'email': 'nobody@x.com'})
        self.assertEqual(self.mailer.sent, [])

    def test_the_code_is_stored_only_as_a_hash_and_never_shown(self):
        self.ask()
        user = self.store.fetch('users', email='ann@x.com')
        self.assertNotIn(self.mailer.code(), str(user['reset']))
        self.assertNotIn('reset', accounts.public(user))

    def test_asking_is_limited(self):
        for _ in range(3):
            self.ask()
        self.assertEqual(self.ask()['message'], 'too many attempts: try again in 60 minutes')

    def test_no_email_settings_no_resets(self):
        hub = Hub(self.store)
        hub.connect('d')
        [(_, m)] = hub.handle('d', {'type': 'request_reset', 'email': 'ann@x.com'})
        self.assertEqual(m['message'], "password resets aren't set up on this server: ask an admin")


class TestResetting(ResetTest):
    def test_the_right_code_sets_the_password_and_logs_in_ending_other_logins(self):
        self.ask()
        m = self.reset(self.mailer.code())
        self.assertEqual((m['type'], m['user']['player_name']), ('logged_in', 'Ann'))
        self.assertIsNone(accounts.login_with_token(self.store, self.old_token))
        self.assertIsNotNone(accounts.login_with_token(self.store, m['token']))
        accounts.login(self.store, 'ann@x.com', 'new pw')
        with self.assertRaises(StoreError):
            accounts.login(self.store, 'ann@x.com', 'old')

    def test_a_code_works_once(self):
        self.ask()
        code = self.mailer.code()
        self.reset(code)
        self.assertEqual(self.reset(code, 'again')['message'], accounts.RESET_WRONG)

    def test_wrong_codes_use_it_up(self):
        self.ask()
        code = self.mailer.code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(accounts.RESET_TRIES):
            m = self.reset(wrong)
            self.assertEqual((m['type'], m['message']), ('login_failed', accounts.RESET_WRONG))
        self.assertEqual(self.reset(code)['message'], accounts.RESET_WRONG)  # (the right one no longer works)

    def test_a_code_expires(self):
        self.ask()
        code = self.mailer.code()
        user = self.store.fetch('users', email='ann@x.com')
        user['reset']['expires'] = (datetime.datetime.now(datetime.timezone.utc)
                                    - datetime.timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.store.update('users', user, {'id': user['id']})
        self.assertEqual(self.reset(code)['message'], accounts.RESET_WRONG)

    def test_the_new_password_must_be_usable(self):
        self.ask()
        self.assertEqual(self.reset(self.mailer.code(), '')['message'], 'a password needs at least one character')

    def test_a_locked_account_gets_no_code(self):
        admin.set_locked(self.store, self.store.fetch('users', email='ann@x.com')['id'], True)
        self.assertEqual(self.ask()['type'], 'reset_requested')
        self.assertEqual(self.mailer.sent, [])


class TestMailerSettings(unittest.TestCase):
    def test_settings_must_be_complete(self):
        with self.assertRaises(ValueError):
            Mailer({'host': 'smtp.example.com', 'port': 587})
        m = Mailer({'host': 'h', 'port': 587, 'username': 'u', 'password': 'p', 'from': 'GC <noreply@mail.example.com>'})
        msg = m.message('ann@x.com', 'Subject', 'Body')
        self.assertEqual((msg['From'], msg['To']), ('GC <noreply@mail.example.com>', 'ann@x.com'))
        self.assertTrue(msg['Message-ID'].endswith('@mail.example.com>'))

    def test_no_file_no_mailer(self):
        self.assertIsNone(Mailer.from_file('no/such/email.json'))


if __name__ == '__main__':
    unittest.main()
