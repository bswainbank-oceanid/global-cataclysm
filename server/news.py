"""
The news the main menu's News screen shows: data/news.json, written by tools/import_news.py from
reference/GC72_News.odt -- {"title", "entries": [{"id", "date", "title", "paragraphs"}]}, newest first.
Read again whenever the file changes, so a new entry needs only the file (an update of the server), not a new
client.
"""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'news.json')

_cache = {'path': None, 'mtime': None, 'doc': None}


def load(path=PATH):
    """The news ({"title", "entries"}); no entries when there's no news file."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {'title': 'News', 'entries': []}
    if _cache['path'] != path or _cache['mtime'] != mtime:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
        _cache.update(path=path, mtime=mtime, doc=doc)
    return _cache['doc']
