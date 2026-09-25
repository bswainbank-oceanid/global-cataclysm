"""
Derive derived/faction_territory_profile.json: for each faction, its list
of owned land territories with the fields the setup-design tools need --
stacking cap, coastal flag, default sea zone, and whether it has a foreign
neighbor.

Default sea zone is the NEAREST (by pixel distance) sea-type neighbor
among a territory's direct graph neighbors -- not simply the first one in
the graph's original (arbitrary Delaunay-build) order. That original
"first in order" rule is what data/scenarios/starting_setup_125ipc.json's
naval defaults were historically built against (see the note preserved in
docs/SCHEMA.md's history), but it picks a geometrically-wrong zone for 34
of the map's 75 coastal territories -- in the worst case (Rocky Mountain
States) a zone 3400px away over one 117px away. Nearest-by-distance is a
strictly more correct rule given only positions + the adjacency graph (no
per-space equivalent, this project has no coastline polygon to check
against directly).

Run from the repo root, after compute_foreign_neighbors.py:
    python3 tools/compute_faction_profile.py
"""
import json

import argparse

import tool_data

_parser = argparse.ArgumentParser()
_parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
tool_data.use_scenario(_parser.parse_args().scenario)

territories = tool_data.spaces()
adjacency = tool_data.adjacency()
foreign = json.load(open(tool_data.root_path('derived/adjacency_foreign.json')))['foreign']

by_id = {s['id']: s for s in territories}
neighbors_ordered = {int(k): v for k, v in adjacency['neighbors_ordered'].items()}
SC_BONUS = tool_data.config().sc_bonus()
SEA_DEPLOYMENT = tool_data.sea_deployment()

land = [s for s in territories if s['type'] == 'land']
factions = sorted({s['faction'] for s in land if s['faction']})

profile = {fac: [] for fac in factions}
for terr in land:
    if not terr['faction']:
        continue  # unassigned (e.g. a newly-added territory with no owner yet)
    tid = terr['id']
    value = terr['value']
    sc = terr['strategic_center']
    cap = value + 3 + (SC_BONUS if sc else 0)
    # coastal / its starting sea zone: the faction assignment's sea_deployment_location_id
    sea_neighbors = [n for n in neighbors_ordered.get(tid, []) if by_id[n]['type'] == 'sea']
    coastal = len(sea_neighbors) > 0
    sea_zone = SEA_DEPLOYMENT.get(tid) if coastal else None
    sea_zone_name = by_id[sea_zone]['name'] if coastal else None
    profile[terr['faction']].append({
        'id': tid,
        'name': terr['name'],
        'value': value,
        'sc': sc,
        'cap': cap,
        'coastal': coastal,
        'sea_zone': sea_zone,
        'sea_zone_name': sea_zone_name,
        'has_foreign_neighbor': len(foreign.get(str(tid), [])) > 0,
    })

json.dump(profile, open(tool_data.root_path('derived/faction_territory_profile.json'), 'w'), indent=2)
print('wrote derived/faction_territory_profile.json:', {k: len(v) for k, v in profile.items()})
