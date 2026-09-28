"""Saved scenario setups (server/setups.py) and the host's save_setup / delete_setup messages."""
import copy
import shutil
import tempfile
import unittest

from engine.repository import ModuleRepository
from engine.module_validation import validate_repository
from server import setups
from server.host import GameHost
from server.lobby import LobbyError


def new_settings(**kw):
    seats = [{'mode': 'HUMAN', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random'},
             {'mode': 'BOT', 'faction': 'NAA', 'alliance': 0, 'strategy': 'adversarial', 'behavior': 'loyal',
              'initial_mpc': 150, 'units_mpc': 120}]
    seats += [{'mode': 'NOT_PLAYING', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random'}] * 4
    return {'seats': seats, 'randomize_order': True, 'max_alliance_size': 1,
            'scenario': {'kind': 'new', 'options': {'sc_bonus': 3}, 'neutral': {'units_mpc': 80}}, **kw}


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        base = ModuleRepository()
        shutil.copytree(base.root, self.dir, dirs_exist_ok=True)
        shutil.rmtree(f'{self.dir}/scenario_setup', ignore_errors=True)
        self.repo = ModuleRepository(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSaving(SetupTest):
    def test_a_saved_setup_lists_with_its_settings_but_no_seed(self):
        sid = setups.save({'id': None, 'name': 'Duel', 'description': 'Two players',
                           'settings': new_settings(seed=5, dev={'combat_first_turn': True})}, self.repo)
        [row] = setups.listing(self.repo)
        self.assertEqual((row['id'], row['name'], row['description']), (sid, 'Duel', 'Two players'))
        self.assertNotIn('seed', row['settings'])
        self.assertNotIn('dev', row['settings'])
        self.assertEqual(row['settings']['seats'][1]['units_mpc'], 120)
        self.assertEqual(validate_repository(self.repo), [])

    def test_the_same_name_updates_and_a_new_name_makes_a_new_setup(self):
        a = setups.save({'id': None, 'name': 'Duel', 'settings': new_settings()}, self.repo)
        changed = new_settings(randomize_order=False)
        self.assertEqual(setups.save({'id': a, 'name': 'Duel', 'description': 'now fixed order', 'settings': changed},
                                     self.repo), a)
        b = setups.save({'id': a, 'name': 'Duel II', 'settings': new_settings()}, self.repo)
        self.assertNotEqual(a, b)
        by_id = {r['id']: r for r in setups.listing(self.repo)}
        self.assertEqual(sorted(by_id), sorted([a, b]))
        self.assertFalse(by_id[a]['settings']['randomize_order'])
        self.assertEqual(by_id[a]['description'], 'now fixed order')

    def test_names_are_unique_and_required(self):
        setups.save({'id': None, 'name': 'Duel', 'settings': new_settings()}, self.repo)
        for name in ('duel', '', '   '):
            with self.assertRaises(LobbyError):
                setups.save({'id': None, 'name': name, 'settings': new_settings()}, self.repo)

    def test_the_fixed_scenario_and_unstartable_settings_are_not_saved(self):
        fixed = new_settings()
        del fixed['scenario']
        with self.assertRaises(LobbyError):
            setups.save({'name': 'Fixed', 'settings': fixed}, self.repo)
        lonely = new_settings()
        lonely['seats'][1] = dict(lonely['seats'][1], mode='NOT_PLAYING')
        with self.assertRaises(LobbyError):
            setups.save({'name': 'Lonely', 'settings': lonely}, self.repo)

    def test_delete(self):
        a = setups.save({'name': 'Duel', 'settings': new_settings()}, self.repo)
        setups.delete(a, self.repo)
        self.assertEqual(setups.listing(self.repo), [])
        with self.assertRaises(LobbyError):
            setups.delete(a, self.repo)


class TestHostMessages(SetupTest):
    def test_save_and_delete_reply_with_the_list(self):
        host = GameHost(repo=self.repo)
        self.assertEqual(host.lobby_message()['setups'], [])
        [reply], _, _ = host.handle({'type': 'save_setup', 'setup': {'name': 'Duel', 'settings': new_settings()}})
        self.assertEqual(reply['type'], 'setups')
        self.assertEqual([r['id'] for r in reply['setups']], [reply['selected']])
        self.assertEqual(len(host.lobby_message()['setups']), 1)
        [reply], _, _ = host.handle({'type': 'delete_setup', 'id': reply['selected']})
        self.assertEqual((reply['setups'], reply['selected']), ([], None))

    def test_a_bad_save_is_an_error(self):
        [reply], _, _ = GameHost(repo=self.repo).handle({'type': 'save_setup', 'setup': {'name': ''}})
        self.assertEqual(reply['type'], 'error')

    def test_a_saved_setup_starts_a_game(self):
        host = GameHost(repo=self.repo)
        [reply], _, _ = host.handle({'type': 'save_setup', 'setup': {'name': 'Duel', 'settings': new_settings()}})
        s = copy.deepcopy(reply['setups'][0]['settings'])
        s['seed'] = 3
        _, broadcast, _ = host.handle({'type': 'new_game', 'settings': s})
        self.assertEqual(broadcast[0]['type'], 'game_started')


if __name__ == '__main__':
    unittest.main()
