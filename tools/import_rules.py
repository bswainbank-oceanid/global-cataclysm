"""
Reads the player's rule book, reference/GC_72 Rules.odt, into data/rules_text.json, which the client's Rules
screen shows (tools/sync_client_data.py copies it into client/data). Run it again after the document
changes:

    python tools/import_rules.py [--source PATH]

data/rules_text.json: {"title", "subtitle", "sections": [{"heading", "blocks": [...]}]}, each block one of
    {"kind": "subheading", "text"}                         (the document's Heading 4, or a paragraph all in bold)
    {"kind": "paragraph", "text"}
    {"kind": "list", "items": [{"text", "items": [...]}]}   (bullets, nested)
    {"kind": "table", "header": [...], "rows": [[...], ...]} (the Units table: its first column is the unit
                                                             type, which the screen shows with its icon)
The title is the document's Heading 1, the subtitle its Subtitle, and each Heading 3 opens a section.
(Not data/rules.json: that one is the engine's rule set.)
"""
import argparse
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'reference', 'GC_72 Rules.odt')
OUT = os.path.join(ROOT, 'data', 'rules_text.json')

NS = {'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
      'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
      'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'}


def _q(prefix, tag):
    return '{%s}%s' % (NS[prefix], tag)


def _text(el):
    """An element's text, spaces and breaks as written, whitespace tidied."""
    out = []

    def walk(e):
        if e.tag == _q('text', 's'):
            out.append(' ' * int(e.get(_q('text', 'c'), '1')))
        elif e.tag in (_q('text', 'tab'), _q('text', 'line-break')):
            out.append(' ')
        elif e.text:
            out.append(e.text)
        for child in e:
            walk(child)
            if child.tail:
                out.append(child.tail)

    walk(el)
    text = ''.join(out).replace('�', '’')  # (an apostrophe the document lost in an export)
    return re.sub(r'\s+', ' ', text).strip()


def _styles(root):
    """Each automatic paragraph style's parent (P21 -> Heading_20_4, say), and the bold text styles."""
    parents, bold = {}, set()
    for s in root.iter(_q('style', 'style')):
        name, parent = s.get(_q('style', 'name')), s.get(_q('style', 'parent-style-name'))
        if parent:
            parents[name] = parent
        props = s.find('style:text-properties', NS)
        if props is not None and props.get('{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}font-weight') == 'bold':
            bold.add(name)
    return parents, bold


def _all_bold(p, bold):
    """Whether every bit of a paragraph's text is in a bold span (how the document writes most subheadings)."""
    if (p.text or '').strip():
        return False
    for child in p:
        if (child.tail or '').strip():
            return False
        if _text(child) and not (child.tag == _q('text', 'span') and child.get(_q('text', 'style-name')) in bold):
            return False
    return True


def _list(el):
    """A text:list as [{"text", "items"}]: an item's first paragraph is its text, its own lists its items. A
    list item holding only a list (the document's way of indenting) adds that list's items one level down."""
    items = []
    for li in el.findall('text:list-item', NS):
        texts = [_text(p) for p in li if p.tag in (_q('text', 'p'), _q('text', 'h'))]
        texts = [t for t in texts if t]
        children = []
        for sub in li.findall('text:list', NS):
            children += _list(sub)
        if texts:
            items.append({'text': ' '.join(texts), 'items': children})
        elif items:
            items[-1]['items'] += children
        else:
            items += children
    return items


def _table(el):
    rows = []
    for tr in el.iter(_q('table', 'table-row')):
        cells = []
        for td in tr.findall('table:table-cell', NS):
            cells.append(' '.join(t for t in (_text(p) for p in td.iter(_q('text', 'p'))) if t))
        if any(cells):
            rows.append(cells)
    return {'kind': 'table', 'header': rows[0] if rows else [], 'rows': rows[1:]}


def rules(path):
    root = ET.fromstring(zipfile.ZipFile(path).read('content.xml'))
    styles, bold = _styles(root)
    body = root.find('office:body/office:text', NS)
    doc = {'title': '', 'subtitle': '', 'sections': []}

    def blocks():
        if not doc['sections']:
            doc['sections'].append({'heading': '', 'blocks': []})
        return doc['sections'][-1]['blocks']

    for el in body:
        if el.tag in (_q('text', 'p'), _q('text', 'h')):
            text = _text(el)
            if not text:
                continue
            style = el.get(_q('text', 'style-name'), '')
            style = styles.get(style, style)
            if style == 'Heading_20_1' and not doc['title']:
                doc['title'] = text
            elif style == 'Subtitle':
                doc['subtitle'] = text
            elif style in ('Heading_20_2', 'Heading_20_3'):
                doc['sections'].append({'heading': text, 'blocks': []})
            elif style == 'Heading_20_4' or el.get(_q('text', 'style-name')) in bold or _all_bold(el, bold):
                blocks().append({'kind': 'subheading', 'text': text})
            else:
                blocks().append({'kind': 'paragraph', 'text': text})
        elif el.tag == _q('text', 'list'):
            items = _list(el)
            if items:
                b = blocks()
                if b and b[-1]['kind'] == 'list':  # (one list the document broke in two)
                    b[-1]['items'] += items
                else:
                    b.append({'kind': 'list', 'items': items})
        elif el.tag == _q('table', 'table'):
            blocks().append(_table(el))
    if not doc['sections']:
        raise ValueError(f'{path} has no sections')
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=SOURCE)
    args = parser.parse_args(argv)
    doc = rules(args.source)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f"wrote {OUT}: {len(doc['sections'])} sections")
    return 0


if __name__ == '__main__':
    sys.exit(main())
