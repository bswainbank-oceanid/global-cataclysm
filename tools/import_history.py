"""
Reads the game's back story, reference/GC_ 1972 History.odt, into data/history.json, which the client's
History screen shows (tools/sync_client_data.py copies it into client/data). Run it again after the
document changes:

    python tools/import_history.py [--source PATH]

data/history.json: {"title": ..., "sections": [{"year": "1941", "headline": "War in Europe! ...",
"paragraphs": [...]}, ...]}. A paragraph starting "<year> - " opens a section (the document's own heading
styles aren't used: they vary); the first paragraph is the title.
"""
import argparse
import html
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'reference', 'GC_ 1972 History.odt')
OUT = os.path.join(ROOT, 'data', 'history.json')
SECTION = re.compile(r'^(\d{4})\s*[-–—]\s*(.+)$')


def paragraphs(path):
    """The document's paragraphs (and headings), as plain text, in order; empty ones left out."""
    xml = zipfile.ZipFile(path).read('content.xml').decode('utf-8')
    out = []
    for _, body in re.findall(r'<text:(p|h)\b[^>]*>(.*?)</text:\1>', xml, re.S):
        body = re.sub(r'<text:s(?: text:c="(\d+)")?/>', lambda m: ' ' * int(m.group(1) or 1), body)
        body = re.sub(r'<text:(?:tab|line-break)/>', ' ', body)
        text = html.unescape(re.sub(r'<[^>]+>', '', body))
        text = re.sub(r'\s+', ' ', text).strip()
        if text:
            out.append(text)
    return out


def history(path):
    paras = paragraphs(path)
    if not paras:
        raise ValueError(f'{path} has no text')
    doc = {'title': paras[0], 'sections': []}
    for p in paras[1:]:
        m = SECTION.match(p)
        if m:
            doc['sections'].append({'year': m.group(1), 'headline': m.group(2).strip(), 'paragraphs': []})
        elif doc['sections']:
            doc['sections'][-1]['paragraphs'].append(p)
        else:  # (text before the first dated section: an introduction)
            doc.setdefault('introduction', []).append(p)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=SOURCE)
    args = parser.parse_args(argv)
    doc = history(args.source)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f"wrote {OUT}: {len(doc['sections'])} sections")
    return 0


if __name__ == '__main__':
    sys.exit(main())
