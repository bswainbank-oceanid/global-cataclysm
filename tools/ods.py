"""
Minimal OpenDocument spreadsheet (.ods) reading and writing -- stdlib only, enough for
the module sheets (tools/module_sheets.py): named sheets of rows of plain cells (text,
numbers, booleans, empty), a bold header row, sensible column widths.

write_ods(path, {sheet name: [[cell, ...], ...]}) -- cells are str / int / float / bool / None.
read_ods(path) -> {sheet name: [[cell, ...], ...]} with the same cell types (numbers come
back as int when whole), trailing empty cells and rows dropped.
"""
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
}
_T = '{%s}' % NS['table']
_O = '{%s}' % NS['office']
_X = '{%s}' % NS['text']

_MANIFEST = '''<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
'''

_STYLES = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles xmlns:office="{office}" xmlns:style="{style}" xmlns:fo="{fo}" office:version="1.2">
 <office:styles><style:default-style style:family="table-cell"><style:paragraph-properties/></style:default-style></office:styles>
</office:document-styles>
'''.format(**NS)

_MAX_WIDTH_CM = 12.0


def _cell_xml(v, header):
    style = ' table:style-name="hdr"' if header else ' table:style-name="txt"'
    if v is None or v == '':
        return '<table:table-cell/>'
    if isinstance(v, bool):
        return (f'<table:table-cell{style} office:value-type="boolean" office:boolean-value="{"true" if v else "false"}">'
                f'<text:p>{"TRUE" if v else "FALSE"}</text:p></table:table-cell>')
    if isinstance(v, (int, float)):
        return f'<table:table-cell{style} office:value-type="float" office:value="{v!r}"><text:p>{v!r}</text:p></table:table-cell>'
    paras = ''.join(f'<text:p>{escape(line)}</text:p>' for line in str(v).split('\n'))
    return f'<table:table-cell{style} office:value-type="string">{paras}</table:table-cell>'


def _widths(rows):
    cols = max((len(r) for r in rows), default=0)
    out = []
    for c in range(cols):
        longest = max((len(str(r[c])) for r in rows if c < len(r) and r[c] is not None), default=4)
        out.append(min(_MAX_WIDTH_CM, max(1.6, 0.21 * longest + 0.4)))
    return out


def write_ods(path, sheets):
    col_styles, tables = [], []
    for si, (name, rows) in enumerate(sheets.items()):
        cols = []
        for ci, w in enumerate(_widths(rows)):
            sname = f'co{si}_{ci}'
            col_styles.append(f'<style:style style:name="{sname}" style:family="table-column">'
                              f'<style:table-column-properties style:column-width="{w:.2f}cm"/></style:style>')
            cols.append(f'<table:table-column table:style-name="{sname}"/>')
        body = []
        for ri, row in enumerate(rows):
            body.append('<table:table-row>' + ''.join(_cell_xml(v, ri == 0) for v in row) + '</table:table-row>')
        tables.append(f'<table:table table:name="{escape(name, {chr(34): "&quot;"})}">' + ''.join(cols) + ''.join(body)
                      + '</table:table>')
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<office:document-content xmlns:office="{office}" xmlns:table="{table}" xmlns:text="{text}" '
               'xmlns:style="{style}" xmlns:fo="{fo}" office:version="1.2">'.format(**NS)
               + '<office:automatic-styles>'
               + '<style:style style:name="hdr" style:family="table-cell">'
                 '<style:table-cell-properties fo:background-color="#dde4ee"/>'
                 '<style:text-properties fo:font-weight="bold"/></style:style>'
               + '<style:style style:name="txt" style:family="table-cell">'
                 '<style:table-cell-properties style:vertical-align="top"/></style:style>'
               + ''.join(col_styles)
               + '</office:automatic-styles><office:body><office:spreadsheet>'
               + ''.join(tables)
               + '</office:spreadsheet></office:body></office:document-content>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.oasis.opendocument.spreadsheet',
                   compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/manifest.xml', _MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('styles.xml', _STYLES, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('content.xml', content, compress_type=zipfile.ZIP_DEFLATED)


def _text(el):
    """A text:p's text, with <text:s/> spaces, tabs and line breaks."""
    out = [el.text or '']
    for child in el:
        if child.tag == _X + 's':
            out.append(' ' * int(child.get(_X + 'c', '1')))
        elif child.tag == _X + 'tab':
            out.append('\t')
        elif child.tag == _X + 'line-break':
            out.append('\n')
        else:
            out.append(_text(child))
        out.append(child.tail or '')
    return ''.join(out)


def _cell_value(cell):
    vtype = cell.get(_O + 'value-type')
    if vtype in ('float', 'percentage', 'currency'):
        v = float(cell.get(_O + 'value'))
        return int(v) if v == int(v) and 'e' not in cell.get(_O + 'value', '').lower() else v
    if vtype == 'boolean':
        return cell.get(_O + 'boolean-value') == 'true'
    paras = cell.findall(_X + 'p')
    if not paras:
        return None
    text = '\n'.join(_text(p) for p in paras)
    return text if text != '' else None


def read_ods(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    sheets = {}
    for table in root.iter(_T + 'table'):
        rows = []
        for row in table.iter(_T + 'table-row'):
            cells = []
            for cell in row:
                if cell.tag not in (_T + 'table-cell', _T + 'covered-table-cell'):
                    continue
                repeat = int(cell.get(_T + 'number-columns-repeated', '1'))
                value = _cell_value(cell)
                cells.extend([value] * (repeat if value is not None or repeat < 1024 else 0))
            while cells and cells[-1] is None:
                cells.pop()
            repeat = int(row.get(_T + 'number-rows-repeated', '1'))
            rows.extend([cells] * (repeat if cells else min(repeat, 1)))
        while rows and not rows[-1]:
            rows.pop()
        sheets[table.get(_T + 'name')] = rows
    return sheets
