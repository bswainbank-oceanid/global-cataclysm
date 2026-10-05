"""What a player can start from and change (server/scenarios.py): built-ins, shared scenarios (admins
only), a player's own scenarios, and their personal settings with Reset Settings."""
import shutil
import tempfile
import unittest
from unittest import mock

from engine.repository import ModuleRepository
from server import accounts, scenarios, setups
from server.store import Store, StoreError
from server.tests.test_setups import new_settings


def fixed_settings(**kw):
    seats = [{'mode': 'HUMAN', 'faction': 'NAA', 'alliance': 0, 'strategy': 'random', 'behavior': 'random'},
             {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random'}]
    seats += [{'mode': 'NONCOMBATANT', 'faction': 'random', 'alliance': 0, 'strategy': 'random',
               'behavior': 'random'}] * 4
    return {'scenario': {'kind': 'fixed'}, 'seats': seats, 'max_alliance_size': 1, **kw}


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.dir = tempfile.mkdtemp()
        shutil.copytree(ModuleRepository().root, self.dir, dirs_exist_ok=True)
        shutil.rmtree(f'{self.dir}/scenario_setup', ignore_errors=True)
        self.repo = ModuleRepository(self.dir)
        self.store = Store(':memory:')
        self.ann = accounts.register(self.store, 'Ann', '', 'ann@x.com', 'pw')[0]['id']
        self.bob = accounts.register(self.store, 'Bob', '', 'bob@x.com', 'pw')[0]['id']
        self.admin = accounts.register(self.store, 'Admin', '', 'admin@x.com', 'pw')[0]['id']
        accounts.set_admin(self.store, self.admin)
        self.duel = setups.save({'id': None, 'name': 'Duel', 'description': 'two', 'settings': new_settings()}, self.repo)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def listing(self, user):
        return {e['id']: e for e in scenarios.listing(self.store, user, self.repo)}

    def refused(self, fn, *args):
        with self.assertRaises(StoreError) as e:
            fn(*args)
        return e.exception.problems


class TestListing(ScenarioTest):
    def test_the_built_ins_then_shared_then_own_in_order(self):
        scenarios.save_own(self.store, self.ann, {'name': 'Zebra', 'settings': new_settings()})
        scenarios.save_own(self.store, self.ann, {'name': 'apple', 'settings': new_settings()})
        rows = scenarios.listing(self.store, self.ann, self.repo)
        self.assertEqual([(r['kind'], r['name']) for r in rows],
                         [('fixed', None), ('new', None), ('shared', 'Duel'), ('own', 'apple'), ('own', 'Zebra')])

    def test_a_players_own_scenarios_are_theirs_alone(self):
        scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'settings': new_settings()})
        self.assertNotIn('own', [e['kind'] for e in self.listing(self.bob).values()])

    def test_shared_scenarios_list_their_own_settings_until_a_player_saves_theirs(self):
        row = self.listing(self.ann)[self.duel]
        self.assertFalse(row['personal'])
        self.assertEqual(row['settings'], row['defaults'])
        self.assertEqual(row['defaults'], setups.listing(self.repo)[0]['settings'])
        self.assertIsNone(self.listing(self.ann)['fixed']['settings'])  # (the client's defaults)


class TestPersonalSettings(ScenarioTest):
    def test_save_then_reset_on_a_shared_scenario(self):
        mine = new_settings(randomize_order=False, seed=9)
        saved = scenarios.save_settings(self.store, self.ann, self.duel, mine, self.repo)
        self.assertNotIn('seed', saved)
        row = self.listing(self.ann)[self.duel]
        self.assertTrue(row['personal'])
        self.assertFalse(row['settings']['randomize_order'])
        self.assertTrue(row['defaults']['randomize_order'])
        self.assertFalse(self.listing(self.bob)[self.duel]['personal'])  # Bob's are untouched
        self.assertTrue(scenarios.reset_settings(self.store, self.ann, self.duel))
        row = self.listing(self.ann)[self.duel]
        self.assertFalse(row['personal'])
        self.assertEqual(row['settings'], row['defaults'])
        self.assertFalse(scenarios.reset_settings(self.store, self.ann, self.duel))  # nothing left to reset

    def test_saving_again_replaces_them(self):
        scenarios.save_settings(self.store, self.ann, self.duel, new_settings(randomize_order=False), self.repo)
        scenarios.save_settings(self.store, self.ann, self.duel, new_settings(randomize_order=True), self.repo)
        self.assertTrue(self.listing(self.ann)[self.duel]['settings']['randomize_order'])

    def test_gc72_takes_personal_settings_but_only_gc72_ones(self):
        scenarios.save_settings(self.store, self.ann, 'fixed', fixed_settings(), self.repo)
        self.assertEqual(self.listing(self.ann)['fixed']['settings']['seats'][0]['faction'], 'NAA')
        self.assertTrue(self.refused(scenarios.save_settings, self.store, self.ann, 'fixed', new_settings(), self.repo))
        self.assertTrue(self.refused(scenarios.save_settings, self.store, self.ann, self.duel, fixed_settings(), self.repo))
        scenarios.save_settings(self.store, self.ann, 'new', new_settings(), self.repo)

    def test_settings_that_couldnt_start_a_game_are_refused(self):
        bad = new_settings()
        bad['seats'][0]['faction'] = 'XYZ'
        self.assertTrue(self.refused(scenarios.save_settings, self.store, self.ann, self.duel, bad, self.repo))
        self.assertEqual(self.refused(scenarios.save_settings, self.store, self.ann, 'Setup_404', new_settings(), self.repo),
                         ["there is no scenario 'Setup_404'"])

    def test_an_own_scenario_has_no_separate_settings(self):
        sid = scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'settings': new_settings()})
        self.assertTrue(self.refused(scenarios.save_settings, self.store, self.ann, sid, new_settings(), self.repo))


class TestOwnScenarios(ScenarioTest):
    def test_same_id_and_name_updates_a_new_name_saves_as_new(self):
        sid = scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'description': 'v1', 'settings': new_settings()})
        self.assertEqual(sid, 'S_000001')
        again = scenarios.save_own(self.store, self.ann, {'id': sid, 'name': 'Mine', 'description': 'v2',
                                                          'settings': new_settings(randomize_order=False)})
        self.assertEqual(again, sid)
        row = self.listing(self.ann)[sid]
        self.assertEqual((row['description'], row['settings']['randomize_order']), ('v2', False))
        copy_id = scenarios.save_own(self.store, self.ann, {'id': sid, 'name': 'Mine 2', 'settings': new_settings()})
        self.assertNotEqual(copy_id, sid)
        self.assertEqual(len([e for e in self.listing(self.ann).values() if e['kind'] == 'own']), 2)

    def test_saving_from_a_shared_scenario_makes_an_own_one(self):
        sid = scenarios.save_own(self.store, self.ann, {'id': self.duel, 'name': 'My duel', 'settings': new_settings()})
        self.assertEqual(self.listing(self.ann)[sid]['kind'], 'own')
        self.assertEqual(setups.listing(self.repo)[0]['name'], 'Duel')  # the shared one is untouched

    def test_names_are_unique_per_player_not_across_players(self):
        scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'settings': new_settings()})
        self.assertEqual(self.refused(scenarios.save_own, self.store, self.ann, {'name': 'MINE', 'settings': new_settings()}),
                         ['you already have a scenario with that name'])
        scenarios.save_own(self.store, self.bob, {'name': 'Mine', 'settings': new_settings()})

    def test_someone_elses_scenario_is_never_changed_saving_makes_your_own_copy(self):
        anns = scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'description': 'Ann', 'settings': new_settings()})
        bobs = scenarios.save_own(self.store, self.bob, {'id': anns, 'name': 'Mine', 'description': 'Bob',
                                                         'settings': new_settings()})
        self.assertNotEqual(bobs, anns)
        self.assertEqual(scenarios.get_own(self.store, anns)['description'], 'Ann')

    def test_only_a_new_scenarios_settings_can_be_saved_and_they_must_be_valid(self):
        self.assertTrue(self.refused(scenarios.save_own, self.store, self.ann, {'name': 'GC', 'settings': fixed_settings()}))
        self.assertEqual(self.refused(scenarios.save_own, self.store, self.ann, {'name': ' ', 'settings': new_settings()}),
                         ['a saved scenario needs a name'])

    def test_a_saved_scenario_keeps_no_seed(self):
        sid = scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'settings': new_settings(seed=4, dev={'x': 1})})
        self.assertNotIn('seed', scenarios.get_own(self.store, sid)['settings'])
        self.assertNotIn('dev', scenarios.get_own(self.store, sid)['settings'])

    def test_only_the_owner_deletes(self):
        sid = scenarios.save_own(self.store, self.ann, {'name': 'Mine', 'settings': new_settings()})
        self.assertEqual(self.refused(scenarios.delete_own, self.store, self.bob, sid), ['you can only delete your own scenarios'])
        scenarios.delete_own(self.store, self.ann, sid)
        self.assertIsNone(scenarios.get_own(self.store, sid))
        self.assertTrue(self.refused(scenarios.delete_own, self.store, self.ann, sid))


class TestSharedScenarios(ScenarioTest):
    def test_only_an_admin_changes_them(self):
        setup = {'id': self.duel, 'name': 'Duel', 'description': 'changed', 'settings': new_settings()}
        self.assertEqual(self.refused(scenarios.save_shared, self.store, self.ann, setup, self.repo),
                         ['only an admin can change the shared scenarios'])
        self.assertTrue(self.refused(scenarios.delete_shared, self.store, self.ann, self.duel, self.repo))
        self.assertEqual(scenarios.save_shared(self.store, self.admin, setup, self.repo), self.duel)
        self.assertEqual(setups.listing(self.repo)[0]['description'], 'changed')

    def test_a_players_own_settings_outlive_an_admins_change(self):
        scenarios.save_settings(self.store, self.ann, self.duel, new_settings(randomize_order=False), self.repo)
        scenarios.save_shared(self.store, self.admin, {'id': self.duel, 'name': 'Duel', 'description': 'new',
                                                       'settings': new_settings()}, self.repo)
        row = self.listing(self.ann)[self.duel]
        self.assertTrue(row['personal'])
        self.assertFalse(row['settings']['randomize_order'])

    def test_deleting_one_drops_everyones_settings_for_it(self):
        scenarios.save_settings(self.store, self.ann, self.duel, new_settings(), self.repo)
        scenarios.delete_shared(self.store, self.admin, self.duel, self.repo)
        self.assertNotIn(self.duel, self.listing(self.ann))
        self.assertEqual(self.store.fetch_all('user_settings'), [])

    def test_gc72_cannot_be_saved_over_even_by_an_admin(self):
        problems = self.refused(scenarios.save_shared, self.store, self.admin,
                                {'id': None, 'name': 'GC72', 'settings': fixed_settings()}, self.repo)
        self.assertEqual(problems, ["only a new scenario's settings can be saved"])


if __name__ == '__main__':
    unittest.main()
