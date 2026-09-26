"""
The module store: every piece of reference data (map, unit set, factions, setups,
rules, bot settings, ...) is a module document, read and written here. See
docs/DATA_MODEL.md.

Today the store is a directory of JSON files, data/modules/<module dir>/<id>.json.
Everything else reads modules through a ModuleRepository, so the files can be
swapped for a database later without touching the engine.

Reading has no side effects beyond caching; documents are returned as parsed, and
callers must treat them as read-only (the same dict is handed to every caller).
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODULES_DIR = os.path.join(ROOT, 'data', 'modules')

# module_type -> directory under the modules root. The order is the order modules
# are listed in tools and reports.
MODULE_DIRS = {
    'Scenario': 'scenario',
    'Map': 'map',
    'AbilityCatalog': 'ability_catalog',
    'UnitSet': 'unit_set',
    'MapValues': 'map_values',
    'FactionSet': 'faction_set',
    'FactionAssignment': 'faction_assignment',
    'SCAssignment': 'sc_assignment',
    'InitialSetup': 'initial_setup',
    'UnitPromotions': 'unit_promotions',
    'RuleSet': 'rule_set',
    'Objectives': 'objectives',
    'PrimaryObjectiveOrder': 'primary_objective_order',
    'StrategyThresholdSet': 'strategy_threshold_set',
    'FactionWeightSet': 'faction_weight_set',
}

DEFAULT_SCENARIO_ID = 'GC72_Scenario'


class ModuleNotFound(KeyError):
    pass


class ModuleRepository:
    def __init__(self, root=None):
        self.root = root or DEFAULT_MODULES_DIR
        self._cache = {}

    def path(self, module_type, module_id):
        return os.path.join(self.root, _dir(module_type), f'{module_id}.json')

    def ids(self, module_type):
        """Every stored id of `module_type`, sorted."""
        d = os.path.join(self.root, _dir(module_type))
        if not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith('.json'))

    def get(self, module_type, module_id):
        key = (module_type, module_id)
        if key not in self._cache:
            path = self.path(module_type, module_id)
            if not os.path.exists(path):
                raise ModuleNotFound(f'no {module_type} module {module_id!r} ({path})')
            with open(path, encoding='utf-8') as f:
                doc = json.load(f)
            if doc.get('module_type') != module_type or doc.get('id') != module_id:
                raise ValueError(f'{path}: holds {doc.get("module_type")} {doc.get("id")!r}, '
                                 f'expected {module_type} {module_id!r}')
            self._cache[key] = doc
        return self._cache[key]

    def all(self, module_type):
        return [self.get(module_type, i) for i in self.ids(module_type)]

    def save(self, doc):
        """Writes `doc` (which names its own module_type and id) and refreshes the cache."""
        module_type, module_id = doc['module_type'], doc['id']
        path = self.path(module_type, module_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dumps(doc))
        self._cache[(module_type, module_id)] = doc

    def clear_cache(self):
        self._cache.clear()


def dumps(doc):
    """The canonical text of a module document: indented JSON, except that a list
    holding only scalars (adjacency, an id list) or only such lists (a polygon's
    points) is written on one line."""
    return _write(doc, 0) + '\n'


def _scalar(v):
    return not isinstance(v, (dict, list))


def _inline(v):
    return isinstance(v, list) and (all(_scalar(x) for x in v) or
                                    all(isinstance(x, list) and all(_scalar(y) for y in x) for x in v))


def _write(v, depth):
    pad, inner = ' ' * depth, ' ' * (depth + 1)
    if isinstance(v, dict):
        if not v:
            return '{}'
        flat = json.dumps(v, ensure_ascii=False, separators=(', ', ': '))
        if len(flat) <= 110 and all(_scalar(x) or (isinstance(x, dict) and all(_scalar(y) for y in x.values()))
                                    for x in v.values()):
            return flat
        items = [f'{inner}{json.dumps(k, ensure_ascii=False)}: {_write(x, depth + 1)}' for k, x in v.items()]
        return '{\n' + ',\n'.join(items) + '\n' + pad + '}'
    if isinstance(v, list):
        if _inline(v):
            return json.dumps(v, ensure_ascii=False, separators=(', ', ': ')).replace('], [', '],[')
        items = [inner + _write(x, depth + 1) for x in v]
        return '[\n' + ',\n'.join(items) + '\n' + pad + ']'
    return json.dumps(v, ensure_ascii=False)


def _dir(module_type):
    if module_type not in MODULE_DIRS:
        raise ValueError(f'unknown module type {module_type!r}')
    return MODULE_DIRS[module_type]


_default = None


def default_repository():
    global _default
    if _default is None:
        _default = ModuleRepository()
    return _default
