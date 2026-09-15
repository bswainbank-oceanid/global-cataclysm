"""
Compute data/adjacency.json from scratch: a Delaunay triangulation over
every space's center point (land and sea together, matching the original
methodology), edges kept under a 420px length threshold.

This REPLACES the old adjacency.json, which was a one-time migration from
a pre-project graph.pkl (see git history / tools/export_adjacency.py) and
was never a regenerable pipeline step -- it silently drifted out of sync
with territories.json as positions changed (Cuba's coordinate fix, the
Tasman Sea split, etc.), and it was never wrap-aware in the first place.

Wraparound: the map is a cylinder, not a torus -- it wraps east-west only
(going off x=3500 comes back around to x=0), north-south does not wrap.
A plain scipy Delaunay triangulation has no concept of that, so this uses
the standard "ghost point" trick for a periodic-in-x triangulation: every
point is triangulated alongside two shifted copies of the whole point set
(-width and +width), an ordinary planar Delaunay runs over all of that,
and only edges that touch at least one real (unshifted) point are kept,
mapped back to real ids and deduplicated. The resulting distance for a
wrap-crossing edge is exactly the cylindrical distance, because the ghost
copy's coordinates already encode it.

Every space in territories.json is now a real graph node -- there is no
more "added after the graph was built" fallback category (that concept
only existed because the old graph was frozen/historical; this one is a
regenerable derived artifact like everything else in derived/, so a
territory added later just gets included next time this runs).

neighbors_ordered's order is *not* meaningful here (sorted by neighbor id
for determinism) -- the historical graph's build-order quirk that some
downstream logic used to depend on ("first sea-type neighbor in this
order") was already replaced by nearest-by-distance in
compute_faction_profile.py; nothing reads order-as-signal anymore.

Run from the repo root:
    python3 tools/compute_adjacency.py
"""
import json

import numpy as np
from scipy.spatial import Delaunay

WIDTH = 3500
THRESHOLD = 420

territories = json.load(open('data/territories.json'))['spaces']

pts = []
owner = []
for shift in (-WIDTH, 0, WIDTH):
    for s in territories:
        pts.append([s['x'] + shift, s['y']])
        owner.append(s['id'])
pts = np.array(pts, dtype=float)

tri = Delaunay(pts)

edges = set()
for simplex in tri.simplices:
    for i in range(3):
        a, b = simplex[i], simplex[(i + 1) % 3]
        ra, rb = owner[a], owner[b]
        if ra == rb:
            continue
        d = np.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
        if d <= THRESHOLD:
            edges.add(tuple(sorted((ra, rb))))

edges = sorted(edges)

neighbors = {}
for a, b in edges:
    neighbors.setdefault(a, set()).add(b)
    neighbors.setdefault(b, set()).add(a)

nodes = {str(s['id']): {'type': s['type'], 'name': s['name']} for s in territories}
neighbors_ordered = {str(tid): sorted(neigh) for tid, neigh in sorted(neighbors.items())}

out = {
    'reference_image_width_px': WIDTH,
    'wraps_east_west': True,
    'node_count': len(nodes),
    'edge_count': len(edges),
    'nodes': nodes,
    'edges': [list(e) for e in edges],
    'neighbors_ordered': neighbors_ordered,
}

json.dump(out, open('data/adjacency.json', 'w'), indent=2)
print(f'wrote data/adjacency.json: {len(nodes)} nodes, {len(edges)} edges')

isolated = [s['id'] for s in territories if s['id'] not in neighbors]
if isolated:
    print('WARNING: territories with zero neighbors:', isolated)
