"""The shared scenarios kept in the player database (server/shared_setups.py), not in the code's files."""
import os

from server import setups
from server.hub import Hub
from server.shared_setups import SharedSetups
from server.tests.test_hub import ONE_HUMAN
from server.tests.test_hub_lobby import LobbyTest
from server.tests.test_setups import new_settings


class TestSharedSetups(LobbyTest):
    def files(self):
        return sorted(os.listdir(f'{self.dir}/modules/scenario_setup'))

    def test_a_new_database_starts_from_the_files_once(self):
        self.assertEqual([s['name'] for s in setups.listing(self.hub.repo)], ['Duel'])
        self.hub.repo.delete(setups.MODULE_TYPE, self.duel)
        again = Hub(self.store, self.repo)  # (a restart: the files aren't copied in a second time)
        self.assertEqual(setups.listing(again.repo), [])

    def test_an_admins_edits_go_to_the_database_not_the_files(self):
        from server import accounts
        accounts.set_admin(self.store, self.ann)
        before = self.files()
        reply = self.only('ann', {'type': 'save_shared', 'scenario': {'name': 'Brawl', 'settings': new_settings()}})
        sid = reply['selected']
        self.assertEqual(self.files(), before)
        self.assertEqual(self.store.fetch('shared_scenarios', id=sid)['name'], 'Brawl')
        self.assertIn('Brawl', [r['name'] for r in reply['scenarios'] if r['kind'] == 'shared'])
        fixed = self.only('ann', {'type': 'save_shared', 'scenario': {'id': 'fixed', 'settings': ONE_HUMAN}})
        self.assertEqual(fixed['type'], 'scenarios', fixed)
        self.assertIsNotNone(self.store.fetch('shared_scenarios', id=setups.FIXED_ID))  # (GC72's defaults too)
        self.only('ann', {'type': 'delete_shared', 'id': sid})
        self.assertIsNone(self.store.fetch('shared_scenarios', id=sid))
        self.assertEqual(self.files(), before)

    def test_other_modules_still_come_from_the_code(self):
        repo = SharedSetups(self.store, self.repo)
        self.assertTrue(repo.ids('ScenarioGenerator'))
        with self.assertRaises(TypeError):
            repo.save({'module_type': 'UnitSet', 'id': 'x'})
