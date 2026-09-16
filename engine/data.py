"""
Loads the static reference data the engine needs: units, rules,
territories, adjacency, factions. Read-only, cached lazily on first
access. Nothing here executes at import time beyond defining functions --
importing this module must never have side effects (a script with
unguarded top-level generation code silently clobbered a hand-tuned
scenario file earlier in this project; every engine module is written to
avoid that class of bug).

Deliberately reads data/*.json directly rather than derived/*.json --
anything derived/ has (coastal flags, default sea zones, foreign-neighbor
lists) is cheap to recompute from territories.json + adjacency.json, and
doing so keeps the engine self-contained instead of depending on the
tools/ pipeline having been run.
"""
import json
import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

_cache = {}


def _load_json(filename):
    with open(os.path.join(_DATA_DIR, filename)) as f:
        return json.load(f)


def units():
    """{unit_type_name: {category, cost, sc_cost, attack_die, damage,
    defense, hp, combat_move, non_combat_move, purchasable, special_abilities}}"""
    if 'units' not in _cache:
        _cache['units'] = _load_json('units.json')['units']
    return _cache['units']


def rules():
    """The full parsed data/rules.json."""
    if 'rules' not in _cache:
        _cache['rules'] = _load_json('rules.json')
    return _cache['rules']


def territories():
    """{territory_id (int): {id, type, name, x, y, faction, value,
    strategic_center, ...}} -- the full space list from territories.json,
    keyed by id for lookup."""
    if 'territories' not in _cache:
        _cache['territories'] = {s['id']: s for s in _load_json('territories.json')['spaces']}
    return _cache['territories']


def adjacency():
    """{territory_id (int): [neighbor territory_id, ...]} from
    adjacency.json's neighbors_ordered. Order carries no meaning (see
    docs/SCHEMA.md) -- treat as a set-like list."""
    if 'adjacency' not in _cache:
        raw = _load_json('adjacency.json')
        _cache['adjacency'] = {int(k): v for k, v in raw['neighbors_ordered'].items()}
    return _cache['adjacency']


def factions():
    """{faction_code: {name, color, major_countries, focus}}"""
    if 'factions' not in _cache:
        _cache['factions'] = _load_json('factions.json')['factions']
    return _cache['factions']


def scenario(name):
    """Loads data/scenarios/<name>.json (e.g. 'starting_setup_200ipc') on
    demand -- not cached at module scope since callers may want distinct
    scenarios in the same process (e.g. tests)."""
    return _load_json(os.path.join('scenarios', f'{name}.json'))


def default_sea_zone(territory_id):
    """The nearest (by pixel distance) sea-type neighbor of a land
    territory among its direct adjacency-graph neighbors, or None if it
    has none (not coastal). Same rule as tools/compute_faction_profile.py
    -- kept in sync deliberately, since both need "which sea zone does a
    coastal territory's naval production default to" and there's no
    coastline-polygon ground truth to check against instead."""
    terrs = territories()
    neighbors = adjacency().get(territory_id, [])
    sea_neighbors = [n for n in neighbors if terrs[n]['type'] == 'sea']
    if not sea_neighbors:
        return None
    here = terrs[territory_id]

    def dist(n):
        there = terrs[n]
        return ((here['x'] - there['x']) ** 2 + (here['y'] - there['y']) ** 2) ** 0.5

    return min(sea_neighbors, key=dist)
