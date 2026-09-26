"""
The scenario's modules (docs/DATA_MODEL.md) in the shapes the map and setup tools work
with, plus the helpers that write tool output back into the modules. Every tool reads
its reference data from here, never from data/*.json directly.

Tools take --scenario (default GC72_Scenario); call use_scenario() with it first.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import data as engine_data  # noqa: E402
from engine.repository import DEFAULT_SCENARIO_ID, default_repository  # noqa: E402,F401


def use_scenario(scenario_id=DEFAULT_SCENARIO_ID):
    return engine_data.use_scenario(scenario_id)


def config():
    return engine_data.config()


def root_path(rel):
    return os.path.join(ROOT, rel)


# ---- reading -----------------------------------------------------------------------------------

def map_meta():
    """{reference_image_width_px, reference_image_height_px, wraps_east_west, image}"""
    info = config().map_info()
    return {'reference_image_width_px': info['width_px'], 'reference_image_height_px': info['height_px'],
            'wraps_east_west': info['wraps_east_west'], 'image': info['image']}


def spaces():
    """Every location as a list of {id, type, name, x, y, area_px, bbox_w, bbox_h, and for land:
    faction, value, strategic_center} -- territories.json's old 'spaces'."""
    return [dict(t) for t in config().territories().values()]


def shapes():
    """{str(location id): [polygon, ...]} -- territory_shapes.json's old 'shapes'."""
    return {str(tid): polys for tid, polys in config().boundaries().items()}


def adjacency():
    """adjacency.json's old shape: {neighbors_ordered: {str(id): [ids]}, edges: [[a, b], ...], nodes}."""
    neighbors = config().adjacency()
    edges = sorted({(min(a, b), max(a, b)) for a, ns in neighbors.items() for b in ns})
    terrs = config().territories()
    return {
        'wraps_east_west': config().map_info()['wraps_east_west'],
        'nodes': {str(t): {'type': terrs[t]['type'], 'name': terrs[t]['name']} for t in terrs},
        'edges': [list(e) for e in edges],
        'neighbors_ordered': {str(t): list(ns) for t, ns in neighbors.items()},
    }


def adjacency_overrides():
    return config().map.get('adjacency_overrides', {'add': [], 'remove': []})


def units():
    """{unit type: stats} (units.json's old 'units', plus abilities)."""
    return config().units()


def factions():
    """{faction id: {name, color, icon}}"""
    return config().factions()


def rules():
    return config().rules()


def unit_weights():
    """{faction: {unit type: weight}} from the scenario's faction weight set."""
    return config().bot_settings()['unit_weights']


def sea_deployment():
    """{land location id: the sea zone its starting naval units deploy to}"""
    return config().sea_deployment()


# ---- writing -----------------------------------------------------------------------------------

def save_map(update):
    """Applies `update(map_doc)` to a copy of the scenario's Map module and saves it."""
    import copy
    repo = default_repository()
    doc = copy.deepcopy(config().map)
    update(doc)
    repo.save(doc)
    engine_data.use_scenario(config().scenario_id)  # drop the views built from the old map
    return repo.path('Map', doc['id'])
