import copy
import json
import os
import shutil
import tempfile
import unittest

from engine.game_config import GameConfig
from engine.module_validation import validate_repository
from engine.repository import DEFAULT_MODULES_DIR, ModuleNotFound, ModuleRepository, dumps


class _TempRepo(unittest.TestCase):
    """A copy of the shipped modules in a temp dir, free to break."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(DEFAULT_MODULES_DIR, os.path.join(self.dir, 'modules'))
        self.repo = ModuleRepository(os.path.join(self.dir, 'modules'))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def edit(self, module_type, module_id, change):
        doc = copy.deepcopy(self.repo.get(module_type, module_id))
        change(doc)
        self.repo.save(doc)
        self.repo.clear_cache()

    def problems(self):
        return validate_repository(self.repo)


class TestShippedModules(unittest.TestCase):
    def test_every_shipped_module_is_valid(self):
        self.assertEqual(validate_repository(ModuleRepository()), [])

    def test_the_canonical_text_round_trips(self):
        repo = ModuleRepository()
        for module_id in repo.ids('UnitSet'):
            doc = repo.get('UnitSet', module_id)
            self.assertEqual(json.loads(dumps(doc)), doc)
            with open(repo.path('UnitSet', module_id), encoding='utf-8') as f:
                self.assertEqual(f.read(), dumps(doc), 'a module file is not in canonical form')

    def test_a_missing_module_is_reported_by_name(self):
        with self.assertRaises(ModuleNotFound):
            ModuleRepository().get('Map', 'NoSuchMap')


class TestGameConfig(unittest.TestCase):
    def setUp(self):
        self.c = GameConfig()

    def test_the_gc72_scenario(self):
        self.assertEqual(len(self.c.territories()), 144)
        self.assertEqual(sum(1 for t in self.c.territories().values() if t['type'] == 'land'), 82)
        self.assertEqual(list(self.c.factions()), ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC'])
        self.assertEqual(self.c.sc_bonus(), 2)
        self.assertEqual(self.c.naval_deploy_excluded(), [43])
        self.assertTrue(self.c.map_info()['wraps_east_west'])

    def test_abilities_resolve_with_catalog_defaults(self):
        units = self.c.units()
        self.assertEqual(units['Aircraft Carrier']['abilities'], {'carrier_air_wing': {'capacity': 3}})
        self.assertEqual(units['Infantry']['abilities']['dig_in'], {'defense_bonus': 1})
        self.assertEqual(units['Infantry']['max_promotions'], 5)
        self.assertEqual(units['Armor']['max_promotions'], 3)
        self.assertEqual(units['Fighter']['air_superiority'], {'attack_die': 'D10', 'damage': 3})

    def test_unlisted_abilities_are_not_in_the_ability_text(self):
        text = self.c.units()['Infantry']['special_abilities']
        self.assertEqual(text, ['Mustering: can be deployed in contested territory', 'Dig In: +1 defense when defending'])

    def test_resolution_order_comes_from_the_unit_types(self):
        from engine.combat import resolution_order
        self.assertEqual(resolution_order(self.c.units(), 'land'), ['Infantry', 'Mechanized Infantry', 'Armor', 'Fighter', 'Bomber'])
        self.assertEqual(resolution_order(self.c.units(), 'sea'), ['Submarine', 'Fighter', 'Bomber', 'Cruiser', 'Aircraft Carrier'])
        self.assertNotIn('resolution_order', self.c.rules()['combat'])

    def test_every_standard_setup_faction_has_three_promotions(self):
        _, promotions = self.c.initial_setup('standard')
        for faction in self.c.factions():
            self.assertEqual(sum(1 for p in promotions['units'] if p['unit_id'].startswith(faction + '-')), 3)


class TestValidation(_TempRepo):
    def test_an_unknown_ability_is_reported(self):
        self.edit('UnitSet', 'GC72_UnitSet', lambda d: d['unit_types'][0]['abilities'].append({'id': 'teleport'}))
        self.assertTrue(any("unknown ability 'teleport'" in p for p in self.problems()))

    def test_an_unknown_ability_parameter_is_reported(self):
        self.edit('UnitSet', 'GC72_UnitSet', lambda d: d['unit_types'][0]['abilities'][0].update(params={'range': 2}))
        self.assertTrue(any("has no parameter 'range'" in p for p in self.problems()))

    def test_a_setup_unit_of_an_unknown_type_is_reported(self):
        self.edit('InitialSetup', 'GC72_StandardSetup',
                  lambda d: d['locations'][0]['units'][0].update(unit_type_id='Battleship'))
        self.assertTrue(any("unknown unit type 'Battleship'" in p for p in self.problems()))

    def test_a_ship_on_land_is_reported(self):
        self.edit('InitialSetup', 'GC72_StandardSetup',
                  lambda d: d['locations'][0]['units'][0].update(unit_type_id='Cruiser'))
        self.assertTrue(any('Cruiser placed on land' in p for p in self.problems()))

    def test_a_missing_reference_is_reported(self):
        self.edit('Scenario', 'GC72_Scenario', lambda d: d.update(rule_set_id='GC72_NoRules'))
        self.assertTrue(any("missing RuleSet 'GC72_NoRules'" in p for p in self.problems()))

    def test_one_sided_adjacency_is_reported(self):
        def cut(d):
            d['locations'][0]['adjacency'] = d['locations'][0]['adjacency'][1:]
        self.edit('Map', 'GC72_Map', cut)
        self.assertTrue(any('adjacency not symmetric' in p for p in self.problems()))

    def test_a_promotion_for_a_unit_not_in_the_setup_is_reported(self):
        self.edit('UnitPromotions', 'GC72_StandardPromotions', lambda d: d['units'].append({'unit_id': 'XX-1', 'num_promotions': 1}))
        self.assertTrue(any("unit 'XX-1' is not in GC72_StandardSetup" in p for p in self.problems()))

    def test_a_faction_without_weights_is_reported(self):
        self.edit('FactionWeightSet', 'GC72_FactionWeights', lambda d: d['factions'].pop())
        self.assertTrue(any('no weights for factions' in p for p in self.problems()))


if __name__ == '__main__':
    unittest.main()
