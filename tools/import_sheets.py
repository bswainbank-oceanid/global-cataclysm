"""
Reads the module spreadsheets (sheets/<ModuleType>.ods) and writes the edits into the
module JSON the game reads (docs/DATA_MODEL.md; layouts in tools/module_sheets.py).

Every module in a workbook is rebuilt from its rows; fields the sheets don't carry
(generated map data: boundaries, adjacency, label metrics) are kept from the JSON. A
module listed in no workbook is left alone. Nothing is written unless every module
still validates afterwards (tools/validate_modules.py's checks).

    python tools/import_sheets.py --check         # list what would change; write nothing
    python tools/import_sheets.py                 # every workbook in sheets/
    python tools/import_sheets.py UnitSet         # just this one
"""
import argparse
import copy
import difflib
import json
import os
import sys

import tool_data  # noqa: F401  (puts the repo root on sys.path)
from engine.module_validation import validate_repository
from engine.repository import MODULE_DIRS, ModuleRepository, default_repository, dumps
from module_sheets import import_workbook
from ods import read_ods

SHEETS_DIR = os.path.join(tool_data.ROOT, 'sheets')


class _Overlay(ModuleRepository):
    """The repository as it would be with `changed` written: for validating before saving."""

    def __init__(self, base, changed):
        super().__init__(base.root)
        self._changed = {(d['module_type'], d['id']): d for d in changed}

    def ids(self, module_type):
        return sorted(set(super().ids(module_type)) | {i for t, i in self._changed if t == module_type})

    def get(self, module_type, module_id):
        return self._changed.get((module_type, module_id)) or super().get(module_type, module_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('types', nargs='*', help='module types (default: every workbook in sheets/)')
    parser.add_argument('--check', action='store_true', help='show what would change; write nothing')
    parser.add_argument('--diff', action='store_true', help='with --check: print the JSON differences too')
    args = parser.parse_args()
    types = args.types or [t for t in MODULE_DIRS if os.path.exists(os.path.join(SHEETS_DIR, f'{t}.ods'))]
    unknown = [t for t in types if t not in MODULE_DIRS]
    if unknown:
        sys.exit(f'unknown module type(s): {", ".join(unknown)}')

    repo = default_repository()
    changed = []
    for module_type in types:
        path = os.path.join(SHEETS_DIR, f'{module_type}.ods')
        existing = {d['id']: d for d in repo.all(module_type)}
        try:
            docs = import_workbook(module_type, read_ods(path), existing)
        except ValueError as e:
            sys.exit(f'{os.path.relpath(path, tool_data.ROOT)}: {e}')
        for mid, doc in docs.items():
            old = existing.get(mid)
            if old is not None and dumps(old) == dumps(doc):
                continue
            changed.append(doc)
            same_data = old is not None and json.loads(dumps(old)) == json.loads(dumps(doc))
            print(f'{"new" if old is None else "reformatted (no data change)" if same_data else "changed"}: '
                  f'{module_type} {mid}')
            if args.diff:
                before = dumps(old).splitlines() if old else []
                sys.stdout.writelines(line + '\n' for line in difflib.unified_diff(
                    before, dumps(doc).splitlines(), f'{mid} (JSON)', f'{mid} (sheet)', lineterm='', n=1))
    if not changed:
        print('no changes: the JSON already matches the sheets')
        return
    problems = validate_repository(_Overlay(repo, changed))
    if problems:
        print('not written -- the result would not validate:')
        for p in problems:
            print('  ' + p)
        sys.exit(1)
    if args.check:
        print(f'{len(changed)} module(s) would change (run without --check to write them)')
        return
    for doc in changed:
        repo.save(copy.deepcopy(doc))
    print(f'wrote {len(changed)} module(s)')


if __name__ == '__main__':
    main()
