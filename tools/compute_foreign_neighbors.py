"""
Derive derived/adjacency_foreign.json from the scenario's map and territory
assignment (tools/tool_data.py): for every land territory, its land-neighbor ids, and
the subset of those belonging to a different faction ("foreign neighbor").

Every land territory is a real node in data/adjacency.json (it's a fully
regenerable graph -- see tools/compute_adjacency.py), so this is a
straight lookup, no distance-threshold fallback needed.

Run from the repo root: python3 tools/compute_foreign_neighbors.py
"""
import json

import argparse

import tool_data

_parser = argparse.ArgumentParser()
_parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
tool_data.use_scenario(_parser.parse_args().scenario)

territories = tool_data.spaces()
adjacency = tool_data.adjacency()

land = {s['id']: s for s in territories if s['type'] == 'land'}
edges = adjacency['edges']

graph_neighbors = {}
for a, b in edges:
    graph_neighbors.setdefault(a, set()).add(b)
    graph_neighbors.setdefault(b, set()).add(a)

adj = {tid: sorted(n for n in graph_neighbors.get(tid, set()) if n in land) for tid in land}

foreign = {}
for tid, neigh in adj.items():
    fac = land[tid]['faction']
    foreign[tid] = [n for n in neigh if land[n]['faction'] != fac]

out = {
    'adj': {str(k): v for k, v in adj.items()},
    'foreign': {str(k): v for k, v in foreign.items()},
}
json.dump(out, open(tool_data.root_path('derived/adjacency_foreign.json'), 'w'), indent=2)
print('wrote derived/adjacency_foreign.json:', len(adj), 'land territories')
