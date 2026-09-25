"""
Checks every module under data/modules/ (docs/DATA_MODEL.md): required fields and
types, unique ids, and every reference between modules. Exits non-zero on a problem.

    python tools/validate_modules.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.module_validation import validate_repository  # noqa: E402
from engine.repository import MODULE_DIRS, ModuleRepository  # noqa: E402


def main():
    repo = ModuleRepository()
    problems = validate_repository(repo)
    count = sum(len(repo.ids(t)) for t in MODULE_DIRS)
    for p in problems:
        print('  ' + p)
    print(f'{count} modules checked: ' + ('no problems.' if not problems else f'{len(problems)} problem(s).'))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
