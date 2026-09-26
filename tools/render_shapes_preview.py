"""
Render exports/territory_shapes_preview.png from data/territory_shapes.json: a
flat vector preview of every territory outline, for eyeballing that the
extracted shapes are right (merged territories, missing islands, sea-zone
splits). Sea zones are filled first, each a stable pseudo-random pastel, then
land territories on top in their controlling faction's colour -- the draw order
territory_shapes.json's docstring requires, so islands cover the sea polygon
that encloses them. Outlines are thin and dark.

Not part of tools/build_all.py: like exports/map.png it is a reference image
(exports/ is gitignored), regenerated on request.

    python tools/render_shapes_preview.py [--out exports/territory_shapes_preview.png]
"""
import argparse
import colorsys

import cv2
import numpy as np

import tool_data

parser = argparse.ArgumentParser()
parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
parser.add_argument('--out', default='exports/territory_shapes_preview.png')
args = parser.parse_args()
tool_data.use_scenario(args.scenario)

territories = {s['id']: s for s in tool_data.spaces()}
shapes = tool_data.shapes()
factions = tool_data.factions()
meta = tool_data.map_meta()
W, H = int(meta['reference_image_width_px']), int(meta['reference_image_height_px'])

OUTLINE = (40, 40, 40)
UNASSIGNED = (150, 150, 150)


def faction_bgr(code):
    if code not in factions:
        return UNASSIGNED
    h = factions[code]['color'].lstrip('#')
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def sea_bgr(tid):
    # A stable, distinct pastel per sea zone (golden-ratio hue walk).
    hue = (tid * 0.61803398875) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.68, 0.45)
    return (int(b * 255), int(g * 255), int(r * 255))


img = np.full((H, W, 3), OUTLINE, np.uint8)  # gaps between shapes read as borders, as in the extracted outlines


def paint(tid, colour):
    for poly in shapes.get(str(tid), []):
        pts = np.array(poly, np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(img, [pts], colour)
        cv2.polylines(img, [pts], True, OUTLINE, 2)


for tid, t in sorted(territories.items()):
    if t['type'] == 'sea':
        paint(tid, sea_bgr(tid))
for tid, t in sorted(territories.items()):
    if t['type'] == 'land':
        paint(tid, faction_bgr(t.get('faction')))

cv2.imwrite(args.out, img)
print(f'wrote {args.out} ({W}x{H})')
