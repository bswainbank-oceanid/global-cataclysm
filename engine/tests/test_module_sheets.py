"""The module spreadsheets (tools/module_sheets.py, tools/ods.py) and the setup tools read the
modules faithfully: exporting and re-importing changes nothing, and an edit made in a sheet
lands in the module."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools')
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from engine.repository import MODULE_DIRS, ModuleRepository, dumps  # noqa: E402
from module_sheets import export_workbook, import_workbook  # noqa: E402
from ods import read_ods, write_ods  # noqa: E402


def names(repo):
    return {loc['id']: loc['name'] for m in repo.all('Map') for loc in m['locations']}


class TestSheets(unittest.TestCase):
    def setUp(self):
        self.repo = ModuleRepository()
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def through_a_file(self, module_type, edit=None):
        """Exports `module_type` to an .ods, optionally edits the sheets, imports it back."""
        existing = {d['id']: d for d in self.repo.all(module_type)}
        sheets = export_workbook(module_type, list(existing.values()), {'names': names(self.repo)})
        if edit:
            edit(sheets)
        path = os.path.join(self.dir, f'{module_type}.ods')
        write_ods(path, sheets)
        return import_workbook(module_type, read_ods(path), existing), existing

    def test_every_module_survives_the_round_trip_exactly(self):
        for module_type in MODULE_DIRS:
            docs, existing = self.through_a_file(module_type)
            self.assertEqual(set(docs), set(existing), module_type)
            for mid in docs:
                self.assertEqual(dumps(docs[mid]), dumps(existing[mid]), f'{module_type} {mid} changed in the round trip')

    def test_an_edited_cost_reaches_the_unit_set(self):
        def edit(sheets):
            rows = sheets['Unit Types']
            col = rows[0].index('cost')
            row = next(r for r in rows if r[1] == 'Armor')
            row[col] = 9
        docs, _ = self.through_a_file('UnitSet', edit)
        armor = next(u for u in docs['GC72_UnitSet']['unit_types'] if u['id'] == 'Armor')
        self.assertEqual(armor['cost'], 9)

    def test_generated_map_fields_are_kept(self):
        def edit(sheets):
            rows = sheets['Locations']
            rows[1][rows[0].index('name')] = 'Polar Sea'
        docs, existing = self.through_a_file('Map', edit)
        new, old = docs['GC72_Map']['locations'][0], existing['GC72_Map']['locations'][0]
        self.assertEqual(new['name'], 'Polar Sea')
        self.assertEqual(new['boundary'], old['boundary'])
        self.assertEqual(new['adjacency'], old['adjacency'])

    def test_an_ability_moves_between_unit_types(self):
        def edit(sheets):
            rows = sheets['Unit Abilities']
            for r in rows[1:]:
                if r[2] == 'bombardment':
                    r[1] = 'Submarine'
        docs, _ = self.through_a_file('UnitSet', edit)
        by_id = {u['id']: u for u in docs['GC72_UnitSet']['unit_types']}
        self.assertIn('bombardment', [a['id'] for a in by_id['Submarine']['abilities']])
        self.assertNotIn('bombardment', [a['id'] for a in by_id['Cruiser']['abilities']])

    def test_a_bad_cell_names_its_sheet_and_row(self):
        def edit(sheets):
            sheets['Values'][3][sheets['Values'][0].index('value')] = 'lots'
        with self.assertRaisesRegex(ValueError, r'Values row 4, value'):  # spreadsheet row 4: the header is row 1
            self.through_a_file('MapValues', edit)

    def test_the_rules_tree_round_trips_prose_and_numbers(self):
        docs, existing = self.through_a_file('RuleSet')
        rules = docs['GC72_Rules']
        self.assertEqual(rules['promotion']['xp_required'], 5)
        self.assertEqual(json.loads(dumps(rules)), json.loads(dumps(existing['GC72_Rules'])))


class TestSetupTools(unittest.TestCase):
    def test_both_setups_validate_as_before(self):
        import tool_data
        import validate_setup
        tool_data.use_scenario()
        setup, _ = tool_data.config().initial_setup('standard')
        self.assertEqual(validate_setup.check_setup(setup, {}), [])
        # The defensive setup's known problems predate the module refactor (see docs/DATA_MODEL.md).
        setup, _ = tool_data.config().initial_setup('defensive')
        errors = validate_setup.check_setup(setup, {})
        self.assertEqual(len(errors), 4, errors)

    def test_a_generated_standard_setup_is_valid(self):
        import generate_setup
        import tool_data
        import validate_setup
        tool_data.use_scenario()
        setup, promotions = tool_data.config().initial_setup('standard')
        profile_path = tool_data.root_path('derived/faction_territory_profile.json')
        if not os.path.exists(profile_path):
            self.skipTest('derived/faction_territory_profile.json not built (tools/build_all.py)')
        with open(profile_path, encoding='utf-8') as f:
            profile = json.load(f)
        new_setup, new_promotions = generate_setup.generate(copy.deepcopy(setup), promotions, profile)
        self.assertEqual(validate_setup.check_setup(new_setup, {}), [])
        self.assertEqual(len(new_promotions['units']), 3 * 6)


if __name__ == '__main__':
    unittest.main()
