"""
Recompute the 'distant' flag on every land territory in data/territories.json
in place: a land territory is "distant" if it has zero same-faction land
neighbors (data/rules.json: map.distant_flag_definition), using the land
adjacency already computed by tools/compute_foreign_neighbors.py.

'distant' is derived data, but (unlike adjacency_foreign.json / the faction
profile) it's stored directly on each space in data/territories.json rather
than in a separate derived/ file, because tools/render_map.py reads it
per-space alongside the rest of a territory's static fields. This script is
what keeps that stored value correct after any edit that can change land
adjacency or faction ownership (most commonly: syncing a faction
reassignment in from the xlsx via tools/sync_territories_from_xlsx.py).

Run from the repo root, after tools/compute_foreign_neighbors.py:
    python3 tools/compute_distant.py
"""
import json

territories = json.load(open('data/territories.json'))
adjf = json.load(open('derived/adjacency_foreign.json'))
adj = adjf['adj']
foreign = adjf['foreign']

changed = 0
for sp in territories['spaces']:
    if sp['type'] != 'land':
        continue
    key = str(sp['id'])
    same_faction_neighbors = len(adj.get(key, [])) - len(foreign.get(key, []))
    is_distant = same_faction_neighbors == 0
    if sp.get('distant') != is_distant:
        changed += 1
    sp['distant'] = is_distant

with open('data/territories.json', 'w') as f:
    json.dump(territories, f, indent=2)

print(f'wrote data/territories.json: recomputed distant flag for all land territories ({changed} changed)')
