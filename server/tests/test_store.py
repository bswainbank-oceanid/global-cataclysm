"""The player database's base (server/store.py): tables, ids, documents, transactions."""
import os
import shutil
import tempfile
import unittest

from server.store import SCHEMA_VERSION, Store, StoreError, utc_now


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.close()

    def add_user(self, uid, email, name):
        self.store.insert('users', {'id': uid, 'email': email, 'player_name': name},
                          id=uid, email=email, player_name=name)


class TestCreating(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'sub', 'players.sqlite3')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_missing_file_and_folder_are_created_with_every_table(self):
        store = Store(self.path)
        tables = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        store.close()
        self.assertTrue(os.path.exists(self.path))
        self.assertTrue({'users', 'logins', 'scenarios', 'user_settings', 'games', 'game_saves', 'chat'} <= tables)

    def test_reopening_keeps_the_data_and_the_schema_version(self):
        store = Store(self.path)
        store.insert('users', {'id': 'U_000001', 'note': 'kept'}, id='U_000001', email='a@x', player_name='A')
        store.close()
        store = Store(self.path)
        self.assertEqual(store.fetch('users', id='U_000001')['note'], 'kept')
        self.assertEqual(store._db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        store.close()

    def test_a_database_from_a_newer_server_is_refused(self):
        store = Store(self.path)
        store._db.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
        store.close()
        with self.assertRaises(RuntimeError):
            Store(self.path)


class TestIds(StoreTest):
    def test_ids_count_up_per_kind_with_their_prefix(self):
        self.assertEqual([self.store.next_id('user') for _ in range(3)], ['U_000001', 'U_000002', 'U_000003'])
        self.assertEqual(self.store.next_id('game'), 'G_000001')
        self.assertEqual(self.store.next_id('scenario'), 'S_000001')

    def test_an_id_is_never_reused_after_a_delete(self):
        uid = self.store.next_id('user')
        self.add_user(uid, 'a@x', 'A')
        self.store.delete('users', id=uid)
        self.assertEqual(self.store.next_id('user'), 'U_000002')


class TestDocuments(StoreTest):
    def test_a_document_round_trips_unchanged(self):
        doc = {'id': 'U_000001', 'nested': {'list': [1, 2.5, None, True]}, 'text': 'Zürich – ✓'}
        self.store.insert('users', doc, id='U_000001', email='a@x', player_name='A')
        self.assertEqual(self.store.fetch('users', id='U_000001'), doc)
        self.assertIsNone(self.store.fetch('users', id='U_999999'))

    def test_update_replaces_the_document_and_its_columns(self):
        self.add_user('U_000001', 'a@x', 'A')
        self.assertTrue(self.store.update('users', {'id': 'U_000001', 'v': 2}, {'id': 'U_000001'}, player_name='B'))
        self.assertEqual(self.store.fetch('users', player_name='B'), {'id': 'U_000001', 'v': 2})
        self.assertFalse(self.store.update('users', {}, {'id': 'U_000009'}))

    def test_upsert_adds_then_replaces(self):
        self.add_user('U_000001', 'a@x', 'A')
        self.store.upsert('user_settings', {'v': 1}, user_id='U_000001', scenario_id='Setup_001')
        self.store.upsert('user_settings', {'v': 2}, user_id='U_000001', scenario_id='Setup_001')
        self.assertEqual(self.store.fetch_all('user_settings', user_id='U_000001'), [{'v': 2}])

    def test_fetch_all_filters_orders_and_limits(self):
        for i, room in enumerate(['browse', 'G_000001', 'browse', 'browse']):
            self.store.insert('chat', {'n': i}, room=room)
        self.assertEqual(self.store.fetch_all('chat', room='browse'), [{'n': 0}, {'n': 2}, {'n': 3}])
        self.assertEqual(self.store.fetch_all('chat', order='id DESC', limit=2, room='browse'), [{'n': 3}, {'n': 2}])
        self.assertEqual(len(self.store.fetch_all('chat', room=['browse', 'G_000001'])), 4)
        self.assertEqual(self.store.fetch_all('chat', room=[]), [])

    def test_chat_rows_are_numbered_in_order(self):
        first = self.store.insert('chat', {}, room='browse')
        second = self.store.insert('chat', {}, room='browse')
        self.assertEqual(second, first + 1)


class TestUniqueness(StoreTest):
    def test_a_taken_email_or_player_name_reads_plainly_whatever_its_case(self):
        self.add_user('U_000001', 'Ann@Example.com', 'Ann')
        with self.assertRaises(StoreError) as e:
            self.add_user('U_000002', 'ann@example.COM', 'Bob')
        self.assertEqual(e.exception.problems, ['that email is already registered'])
        with self.assertRaises(StoreError) as e:
            self.add_user('U_000002', 'bob@example.com', 'ANN')
        self.assertEqual(e.exception.problems, ['that player name is already taken'])

    def test_scenario_names_are_unique_per_owner_only(self):
        self.add_user('U_000001', 'a@x', 'A')
        self.add_user('U_000002', 'b@x', 'B')
        self.store.insert('scenarios', {}, id='S_000001', owner_id='U_000001', name='Duel')
        self.store.insert('scenarios', {}, id='S_000002', owner_id='U_000002', name='Duel')
        with self.assertRaises(StoreError) as e:
            self.store.insert('scenarios', {}, id='S_000003', owner_id='U_000001', name='duel')
        self.assertEqual(e.exception.problems, ['you already have a scenario with that name'])

    def test_a_record_pointing_at_a_missing_user_is_refused(self):
        with self.assertRaises(StoreError):
            self.store.insert('logins', {}, token_hash='abc', user_id='U_000404')

    def test_deleting_a_user_deletes_their_logins(self):
        self.add_user('U_000001', 'a@x', 'A')
        self.store.insert('logins', {}, token_hash='abc', user_id='U_000001')
        self.store.delete('users', id='U_000001')
        self.assertIsNone(self.store.fetch('logins', token_hash='abc'))


class TestTransactions(StoreTest):
    def test_a_failed_transaction_writes_nothing(self):
        with self.assertRaises(StoreError):
            with self.store.transaction():
                self.add_user('U_000001', 'a@x', 'A')
                self.add_user('U_000002', 'a@x', 'B')  # the email is taken: everything is undone
        self.assertIsNone(self.store.fetch('users', id='U_000001'))

    def test_transactions_nest(self):
        with self.store.transaction():
            with self.store.transaction():
                self.add_user('U_000001', 'a@x', 'A')
            self.add_user('U_000002', 'b@x', 'B')
        self.assertEqual(len(self.store.fetch_all('users')), 2)


class TestTime(unittest.TestCase):
    def test_utc_now_is_iso_to_the_second(self):
        self.assertRegex(utc_now(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')


if __name__ == '__main__':
    unittest.main()
