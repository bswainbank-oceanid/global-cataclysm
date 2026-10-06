"""tools/import_rules.py: the rule book (reference/GC_72 Rules.odt) as the Rules screen's data."""
import os
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools')
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from engine import data  # noqa: E402
from import_rules import SOURCE, rules  # noqa: E402


@unittest.skipUnless(os.path.exists(SOURCE), 'the rule book is not here')
class TestImportRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = rules(SOURCE)

    def test_sections_subheadings_and_nested_lists(self):
        self.assertEqual(self.doc['subtitle'], 'Rules of War')
        headings = [s['heading'] for s in self.doc['sections']]
        self.assertEqual(headings[:2], ['Game Objectives', 'Units'])
        subheadings = [b['text'] for s in self.doc['sections'] for b in s['blocks'] if b['kind'] == 'subheading']
        self.assertIn('Military Production Capacity (MPC)', subheadings)  # (a Heading 4)
        self.assertIn('Combat', subheadings)                              # (a paragraph all in bold)
        lists = [b for s in self.doc['sections'] for b in s['blocks'] if b['kind'] == 'list']
        self.assertTrue(any(item['items'] for b in lists for item in b['items']))

    def test_the_units_table_names_unit_types_the_client_has_icons_for(self):
        [table] = [b for s in self.doc['sections'] for b in s['blocks'] if b['kind'] == 'table']
        self.assertEqual(table['header'][0], 'Unit')
        units = [row[0] for row in table['rows']]
        self.assertEqual(set(units), set(data.units()))
        self.assertTrue(all(len(row) == len(table['header']) for row in table['rows']))


if __name__ == '__main__':
    unittest.main()
