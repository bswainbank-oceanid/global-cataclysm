"""
Extract data/territory_shapes.json: real vertex polygons for every land
territory AND sea zone, for the eventual Godot client (Polygon2D fill +
CollisionPolygon2D hit-testing) -- territories.json only has a center
point and bounding box today, not an outline.

Reuses tools/map_geometry.py's classification and connected-component
labeling (the same logic tools/render_map.py uses for the land color-fill
preview), so this and the rendered preview can never silently disagree
about which pixels belong to which territory.

Each territory gets a *list* of polygons, not one flat polygon:
- Six land territories are island chains split across multiple
  disconnected components (Cuba, Falkland Islands, Philippines, New
  Guinea, Hawaii, Polynesia -- see map_geometry.MULTI_SEED_BOX).
- Sea zones are the opposite problem: the base map's inter-sea-zone
  border art has a couple of gaps where two zones share one connected
  component with no drawn line between them. map_geometry.sea_territory_masks
  resolves this generically (nearest-seed-point split within the shared
  component), so it still produces one clean mask per zone.
A single-polygon assumption would silently drop islands, or leave two
sea zones merged into one shape.
"""
import json
import cv2
import numpy as np
from map_geometry import label_land, territory_labels, label_sea, sea_territory_masks, absorb_unclaimed_land

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
# area -- keeps only real landmass/sea pieces per label. Raised from an
# initial 10 after inspecting land output: a handful of near-degenerate
# slivers (as few as 2 vertices post-simplification -- not usable
# polygons) were slipping through at 10.
MIN_CONTOUR_AREA = 25


def polygons_from_mask(mask):
    """mask: boolean array, true for pixels belonging to one territory.
    Returns a list of simplified vertex polygons (usually one)."""
    region = mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for c in contours:
        if cv2.contourArea(c) < MIN_CONTOUR_AREA:
            continue
        approx = cv2.approxPolyDP(c, APPROX_EPSILON, closed=True)
        if len(approx) < 3:
            continue
        polygons.append([[int(pt[0][0]), int(pt[0][1])] for pt in approx])
    return polygons


data = json.load(open(json_path))
spaces = data['spaces']
width = data['reference_image_width_px']

img = cv2.imread(base_path)

land_labels, _, land_centroid_of = label_land(img)
labels_by_territory = territory_labels(spaces, land_labels, land_centroid_of)

sea_labels, _, sea_centroid_of = label_sea(img)
masks_by_sea_territory = sea_territory_masks(spaces, sea_labels, sea_centroid_of)

claimed_land_labels = set()
for labs in labels_by_territory.values():
    claimed_land_labels |= labs
masks_by_sea_territory = absorb_unclaimed_land(masks_by_sea_territory, land_labels, land_centroid_of, claimed_land_labels)

shapes = {}
for sp in spaces:
    tid = sp['id']
    stype = sp.get('type')

    if stype == 'land':
        labels = labels_by_territory.get(tid)
        if not labels:
            print(f'WARNING: no land component found for territory {tid} ({sp.get("name")}) -- skipped')
            continue
        mask = np.isin(land_labels, list(labels))
    elif stype == 'sea':
        mask = masks_by_sea_territory.get(tid)
        if mask is None:
            print(f'WARNING: no sea component found for territory {tid} ({sp.get("name")}) -- skipped')
            continue
    else:
        continue

    polygons = polygons_from_mask(mask)
    if not polygons:
        print(f'WARNING: territory {tid} ({sp.get("name")}) produced no polygon above the area threshold -- skipped')
        continue

    shapes[str(tid)] = polygons

out = {
    'reference_image_width_px': width,
    'note': (
        "Vector outlines for every land territory and sea zone, extracted "
        "from assets/base_map.png's flood-filled regions (see "
        'tools/map_geometry.py). Each territory maps to a LIST of '
        'polygons (usually one; land island-chain territories and a '
        'couple of sea zones with border-art gaps can have several), '
        'each a list of [x, y] vertices in reference-image pixel '
        'coordinates, closed (no repeated last point).'
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

total_count = len([sp for sp in spaces if sp.get('type') in ('land', 'sea')])
missing = total_count - len(shapes)
if missing:
    print(f'{missing} of {total_count} territories missing shapes (see warnings above)')
