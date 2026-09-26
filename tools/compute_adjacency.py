"""
Compute the Map module's adjacency from the real outlines: two spaces are adjacent
when their boundary polygons touch (see tools/outline_adjacency.py: within a few
pixels, wrapping east-west on a cylinder map, a sea zone counting only as its
visible water). Nothing is added by hand and nothing is inferred from centre points.

This replaced a Delaunay triangulation over the spaces' centre points (edges
under a 420px cap, plus a short hand-confirmed FORCED_EDGES list). That method
could not represent large or oddly shaped spaces -- a big sea zone's centre sits
far from most of its coastline -- and produced ~120 edges between spaces whose
outlines do not touch and missed ~125 that do (see tools/debug_adjacency.py).
Consequence to know about: two land spaces separated by water (England/Benelux,
Ireland/Scotland) are NOT adjacent; they connect through the sea zone between.

On top of that, the Map's adjacency_overrides hold hand-confirmed corrections:
'remove' pairs whose outlines touch but that are not adjacent in play, and 'add'
pairs adjacent in play whose outlines do not touch. They are applied last, and
the script says so if an entry has no effect (a stale correction).

Needs the boundaries, so tools/extract_territory_shapes.py runs first
(tools/build_all.py has them in that order). Each location's adjacency list is
sorted by neighbour id; its order carries no meaning.

Run from the repo root:
    python tools/compute_adjacency.py [--scenario GC72_Scenario]
"""
import argparse

import tool_data
from outline_adjacency import label_image, touching_pairs, load

parser = argparse.ArgumentParser()
parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
args = parser.parse_args()
tool_data.use_scenario(args.scenario)

meta, spaces, shapes = load()
WIDTH = int(meta['reference_image_width_px'])
HEIGHT = int(meta['reference_image_height_px'])
territories = meta['spaces']

touching = touching_pairs(label_image(spaces, shapes, WIDTH, HEIGHT), wraps=meta['wraps_east_west'])

overrides = tool_data.adjacency_overrides()
edges = set(touching)
for a, b, *_ in overrides['remove']:
    pair = (min(a, b), max(a, b))
    if pair not in edges:
        print(f'WARNING: override removes {pair}, which is not adjacent anyway (stale?)')
    edges.discard(pair)
for a, b, *_ in overrides['add']:
    pair = (min(a, b), max(a, b))
    if pair in edges:
        print(f'WARNING: override adds {pair}, which is already adjacent (stale?)')
    edges.add(pair)
edges = sorted(edges)

neighbors = {}
for a, b in edges:
    neighbors.setdefault(a, set()).add(b)
    neighbors.setdefault(b, set()).add(a)


def _store(m):
    for loc in m['locations']:
        loc['adjacency'] = sorted(neighbors.get(loc['id'], ()))


path = tool_data.save_map(_store)
print(f'wrote the adjacency of {len(territories)} locations into {path}: {len(edges)} edges '
      f"({len(touching)} from outlines, -{len(overrides['remove'])} removed, +{len(overrides['add'])} added by hand)")

isolated = [s['id'] for s in territories if s['id'] not in neighbors]
if isolated:
    print('WARNING: territories with zero neighbors:', [(i, spaces[i]['name']) for i in isolated])
