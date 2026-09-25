"""
Writes every module type's spreadsheet, sheets/<ModuleType>.ods, from the module JSON
(docs/DATA_MODEL.md; the layouts are in tools/module_sheets.py). Run it after the JSON
changes other than through the sheets -- a tool regenerating the map's boundaries or
adjacency, a setup generator -- so the sheets show what the game reads.

    python tools/export_sheets.py                 # every module type
    python tools/export_sheets.py UnitSet Map     # just these
"""
import argparse
import os
import sys

import tool_data  # noqa: F401  (puts the repo root on sys.path)
from engine.repository import MODULE_DIRS, default_repository
from module_sheets import export_workbook
from ods import write_ods

SHEETS_DIR = os.path.join(tool_data.ROOT, 'sheets')


def location_names(repo):
    names = {}
    for m in repo.all('Map'):
        for loc in m['locations']:
            names.setdefault(loc['id'], loc['name'])
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('types', nargs='*', help=f'module types (default: all of {", ".join(MODULE_DIRS)})')
    args = parser.parse_args()
    unknown = [t for t in args.types if t not in MODULE_DIRS]
    if unknown:
        sys.exit(f'unknown module type(s): {", ".join(unknown)}')
    repo = default_repository()
    ctx = {'names': location_names(repo)}
    os.makedirs(SHEETS_DIR, exist_ok=True)
    for module_type in args.types or MODULE_DIRS:
        docs = repo.all(module_type)
        path = os.path.join(SHEETS_DIR, f'{module_type}.ods')
        write_ods(path, export_workbook(module_type, docs, ctx))
        print(f'wrote {os.path.relpath(path, tool_data.ROOT)}: {len(docs)} module(s)')


if __name__ == '__main__':
    main()
