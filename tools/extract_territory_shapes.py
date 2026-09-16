"""
Extract data/territory_shapes.json: real vertex polygons for every land
territory, for the eventual Godot client (Polygon2D fill +
CollisionPolygon2D hit-testing) -- territories.json only has a center
point and bounding box today, not an outline.

Reuses tools/map_geometry.py's land classification and connected-component
labeling (the same logic tools/render_map.py uses for the color-fill
preview), so this and the rendered preview can never silently disagree
about which pixels belong to which territory.

Sea zones are out of scope here: the base map's inter-sea-zone border art
is sparse/incomplete (a known issue from earlier sea-zone work), so
flood-fill boundaries for sea zones aren't reliable enough to vectorize.

Each territory gets a *list* of polygons, not one flat polygon, because
six territories are island chains split across multiple land components
(Cuba, Falkland Islands, Philippines, New Guinea, Hawaii, Polynesia --
see map_geometry.MULTI_SEED_BOX). A single-polygon assumption would silently
drop every island but the one the seed point sits on.
"""
import json
import cv2
import numpy as np
from map_geometry import label_land, territory_labels

json_path = 'data/territories.json'
base_path = 'assets/base_map.png'
out_path = 'data/territory_shapes.json'

# Vertex simplification tolerance (px), passed to cv2.approxPolyDP. Larger
# = fewer vertices/coarser shape. 2.5px is barely visible at map scale
# (3500x2000) while cutting contour vertex counts by roughly an order of
# magnitude versus the raw pixel-level contour.
APPROX_EPSILON = 2.5

# Drop contours (holes/specks from anti-aliasing noise at territory
# borders, or genuinely tiny offshore rocks a few px across) below this
# area -- keeps only real landmass pieces per label. Raised from an
# initial 10 after inspecting output: a handful of near-degenerate slivers
# (as few as 2 vertices post-simplification -- not usable polygons) were
# slipping through at 10.
MIN_CONTOUR_AREA = 25

data = json.load(open(json_path))
spaces = data['spaces']
width = data['reference_image_width_px']

img = cv2.imread(base_path)
land_labels, num_labels, centroid_of = label_land(img)
labels_by_territory = territory_labels(spaces, land_labels, centroid_of)

shapes = {}
for sp in spaces:
    if sp.get('type') != 'land':
        continue
    tid = sp['id']
    labels = labels_by_territory.get(tid)
    if not labels:
        print(f'WARNING: no land component found for territory {tid} ({sp.get("name")}) -- skipped')
        continue

    region = np.isin(land_labels, list(labels)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    polygons = []
    for c in contours:
        if cv2.contourArea(c) < MIN_CONTOUR_AREA:
            continue
        approx = cv2.approxPolyDP(c, APPROX_EPSILON, closed=True)
        if len(approx) < 3:
            continue
        polygons.append([[int(pt[0][0]), int(pt[0][1])] for pt in approx])

    if not polygons:
        print(f'WARNING: territory {tid} ({sp.get("name")}) produced no polygon above the area threshold -- skipped')
        continue

    shapes[str(tid)] = polygons

out = {
    'reference_image_width_px': width,
    'note': (
        'Vector outlines for land territories only, extracted from '
        "assets/base_map.png's flood-filled regions (see "
        'tools/map_geometry.py). Each territory maps to a LIST of '
        'polygons (usually one; island-chain territories have several), '
        'each a list of [x, y] vertices in reference-image pixel '
        'coordinates, closed (no repeated last point). Sea zones are not '
        'included -- see module docstring.'
    ),
    'approx_epsilon_px': APPROX_EPSILON,
    'territory_count': len(shapes),
    'shapes': shapes,
}
json.dump(out, open(out_path, 'w'), indent=2)

vertex_counts = [len(poly) for polys in shapes.values() for poly in polys]
print(f'wrote {out_path}: {len(shapes)} territories, '
      f'{sum(len(v) for v in shapes.values())} polygons total, '
      f'{sum(vertex_counts)} vertices total '
      f'(min {min(vertex_counts)}, max {max(vertex_counts)}, avg {sum(vertex_counts)/len(vertex_counts):.1f} per polygon)')

land_count = len([sp for sp in spaces if sp.get('type') == 'land'])
missing = land_count - len(shapes)
if missing:
    print(f'{missing} of {land_count} land territories missing shapes (see warnings above)')
