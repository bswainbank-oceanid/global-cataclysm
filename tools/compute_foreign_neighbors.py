"""
Derive derived/adjacency_foreign.json from data/territories.json and
data/adjacency.json: for every land territory, its land-neighbor ids, and
the subset of those belonging to a different faction ("foreign neighbor").

Territories not present in data/adjacency.json (added to the map after the
adjacency graph was originally built) fall back to a center-to-center
distance threshold against every other land territory -- see
data/rules.json: map.adjacency_fallback. Note this fallback is one-directional
as currently implemented: a fallback territory looks outward and finds
graph-based neighbors within range, but a graph-based territory's own
neighbor list is built purely from graph edges and won't include a
fallback territory looking at IT. This reproduces the original ad hoc
calculation faithfully; making it symmetric is a reasonable future fix
(see docs/SCHEMA.md).

Run from the repo root: python3 tools/compute_foreign_neighbors.py
"""
import json
import math

territories = json.load(open('data/territories.json'))['spaces']
adjacency = json.load(open('data/adjacency.json'))
rules = json.load(open('data/rules.json'))['map']

THRESHOLD = rules['reference_image_width_px'] and 420  # px, matches the graph's own calibration

land = {s['id']: s for s in territories if s['type'] == 'land'}
graph_node_ids = {int(k) for k in adjacency['nodes'].keys()}
edges = adjacency['edges']

graph_neighbors = {}
for a, b in edges:
    graph_neighbors.setdefault(a, set()).add(b)
    graph_neighbors.setdefault(b, set()).add(a)


def dist(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


adj = {}
for tid, terr in land.items():
    if tid in graph_node_ids:
        neigh = sorted(n for n in graph_neighbors.get(tid, set()) if n in land)
    else:
        neigh = sorted(
            other_id for other_id, other in land.items()
            if other_id != tid and dist(terr, other) <= THRESHOLD
        )
    adj[tid] = neigh

foreign = {}
for tid, neigh in adj.items():
    fac = land[tid]['faction']
    foreign[tid] = [n for n in neigh if land[n]['faction'] != fac]

out = {
    'adj': {str(k): v for k, v in adj.items()},
    'foreign': {str(k): v for k, v in foreign.items()},
}
json.dump(out, open('derived/adjacency_foreign.json', 'w'), indent=2)
print('wrote derived/adjacency_foreign.json:', len(adj), 'land territories')
