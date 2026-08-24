"""
Export the Delaunay-triangulation adjacency graph (built earlier this
project as a Python pickle, graph.pkl) into a portable JSON file:
data/adjacency.json. This is a one-time migration -- once adjacency.json
exists it is the canonical source, and graph.pkl is no longer needed.

Run from the repo root:  python3 tools/export_adjacency.py <path-to-graph.pkl>
"""
import json
import pickle
import sys

PKL_PATH = sys.argv[1] if len(sys.argv) > 1 else 'graph.pkl'
OUT_PATH = 'data/adjacency.json'

with open(PKL_PATH, 'rb') as f:
    G = pickle.load(f)

graph = G['G']
by_id = G['by_id']

nodes = {str(nid): {'type': attrs['type'], 'name': attrs['name']} for nid, attrs in by_id.items()}
edges = sorted({tuple(sorted((int(a), int(b)))) for a, b in graph.edges()})
edges = [list(e) for e in edges]

# Preserve each node's neighbor list in its ORIGINAL networkx iteration
# order (insertion order from the Delaunay build), not just as a sorted
# edge list. Several already-validated derived values (a territory's
# default naval "sea_zone", used wherever a purchase isn't explicitly
# overridden) were computed as "first sea neighbor in this order" -- so
# this needs to stay reproducible even though the ordering itself is a
# geometric accident of how the graph was built, not a principled rule.
neighbors_ordered = {
    str(nid): [int(n) for n in graph.neighbors(nid)]
    for nid in graph.nodes()
}

out = {
    '_comment': (
        'Adjacency graph exported from the original Delaunay-triangulation '
        'build (graph.pkl). Nodes are the same ids used in data/territories.json '
        '(both land and sea spaces). See data/rules.json map.adjacency_fallback '
        'for the 2 territories added after this graph was built, which are not '
        'present here and need the distance-threshold fallback instead. '
        'neighbors_ordered preserves each node\'s original neighbor order '
        '(an artifact of the Delaunay build, not a principled sort) because '
        'downstream defaults (e.g. a coastal territory\'s default sea zone) '
        'were computed as "first neighbor of this type" and are already '
        'baked into validated scenario data -- edges is the same adjacency '
        'as a clean, order-independent set, for anything that does not need '
        'that legacy behavior.'
    ),
    'reference_image_width_px': G.get('W'),
    'node_count': len(nodes),
    'edge_count': len(edges),
    'nodes': nodes,
    'edges': edges,
    'neighbors_ordered': neighbors_ordered,
}

with open(OUT_PATH, 'w') as f:
    json.dump(out, f, indent=2)

print(f'wrote {OUT_PATH}: {len(nodes)} nodes, {len(edges)} edges')
