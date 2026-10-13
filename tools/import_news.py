"""
Reads the news, reference/GC72_News.odt, into data/news.json, which the server hands the client's News screen
(server/news.py; the main menu's News pulses while an entry is unread). Run it again after adding an entry,
then update the server -- the clients don't need rebuilding:

    python tools/import_news.py [--source PATH]

The document: its first paragraph is the title; then each entry is a table -- a row of [date, title], then a
row with the entry itself (line breaks between paragraphs, links as links). Newest first, as written.

data/news.json: {"title", "entries": [{"id", "date", "title", "paragraphs": [BBCode text, ...]}]}, where
"id" stays the same for as long as the entry's date and title do (what "read" is remembered by), and a
paragraph's links are [url=...]...[/url] (any other "[" written as [lb]).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'reference', 'GC72_News.odt')
OUT = os.path.join(ROOT, 'data', 'news.json')

NS = {'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
      'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
      'xlink': 'http://www.w3.org/1999/xlink'}
BREAK = '\x00'  # (a line break, while a cell's text is gathered)


def _q(prefix, tag):
    return '{%s}%s' % (NS[prefix], tag)


def _tidy(text):
    text = text.replace('�', '’')  # (an apostrophe the document lost in an export)
    return re.sub(r'[ \t\r\n]+', ' ', text)


def _escape(text):
    return text.replace('[', '[lb]')


def _bbcode(el):
    """A paragraph's text as BBCode: links as [url], line breaks as BREAK."""
    out = []

    def walk(e):
        if e.tag == _q('text', 's'):
            out.append(' ' * int(e.get(_q('text', 'c'), '1')))
        elif e.tag == _q('text', 'tab'):
            out.append(' ')
        elif e.tag == _q('text', 'line-break'):
            out.append(BREAK)
        elif e.tag == _q('text', 'a'):
            href = e.get(_q('xlink', 'href'), '')
            label = _tidy(''.join(e.itertext())).strip() or href
            out.append(f'[url={href}]{_escape(label)}[/url]')
            return
        elif e.text:
            out.append(_escape(_tidy(e.text)))
        for child in e:
            walk(child)
            if child.tail:
                out.append(_escape(_tidy(child.tail)))

    walk(el)
    return ''.join(out)


def _paragraphs(cell):
    """An entry's paragraphs: its cell's paragraphs, split again at line breaks (an empty line between)."""
    out = []
    for p in cell.iter(_q('text', 'p')):
        for part in _bbcode(p).split(BREAK):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _cell_text(cell):
    return _tidy(''.join(cell.itertext())).strip()


def news(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    body = root.find('.//office:text', NS)
    title = ''
    entries = []
    for el in body:
        if el.tag == _q('text', 'p') and not title and _cell_text(el):
            title = _cell_text(el)
        elif el.tag == _q('table', 'table'):
            rows = el.findall('table:table-row', NS)
            if len(rows) < 2:
                continue
            head = rows[0].findall('table:table-cell', NS)
            date = _cell_text(head[0]) if head else ''
            name = _cell_text(head[1]) if len(head) > 1 else ''
            paragraphs = []
            for row in rows[1:]:
                for cell in row.findall('table:table-cell', NS):
                    paragraphs += _paragraphs(cell)
            if not (date or name or paragraphs):
                continue
            key = hashlib.sha1(f'{date}|{name}'.encode('utf-8')).hexdigest()[:12]
            entries.append({'id': key, 'date': date, 'title': name, 'paragraphs': paragraphs})
    return {'title': title or 'News', 'entries': entries}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=SOURCE)
    args = parser.parse_args(argv)
    doc = news(args.source)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print(f'wrote {OUT}: {len(doc["entries"])} entries' + (f', newest "{doc["entries"][0]["title"]}"' if doc['entries'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
