"""tools/import_news.py: the news (reference/GC72_News.odt) as the News screen's data; and the copy the server
hands out (data/news.json) is up to date with the document."""
import json
import os
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools')
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from import_news import OUT, SOURCE, news  # noqa: E402


@unittest.skipUnless(os.path.exists(SOURCE), 'the news document is not here')
class TestImportNews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = news(SOURCE)

    def test_entries_newest_first_with_a_date_a_title_and_paragraphs(self):
        self.assertEqual(self.doc['title'], 'News from the Global Cataclysm')
        first = self.doc['entries'][0]
        self.assertEqual(first['title'], 'Welcome to Global Cataclysm: 1972')
        self.assertTrue(first['date'])
        self.assertGreater(len(first['paragraphs']), 1)  # (split at the document's line breaks)
        self.assertEqual(len({e['id'] for e in self.doc['entries']}), len(self.doc['entries']))

    def test_links_stay_links(self):
        text = ' '.join(p for e in self.doc['entries'] for p in e['paragraphs'])
        self.assertIn('[url=https://discord.gg/', text)
        self.assertNotIn('�', text)  # (the lost apostrophes put back)

    def test_the_servers_copy_is_up_to_date(self):
        with open(OUT, encoding='utf-8') as f:
            self.assertEqual(json.load(f), self.doc, 'data/news.json is out of date: run tools/import_news.py')


if __name__ == '__main__':
    unittest.main()
