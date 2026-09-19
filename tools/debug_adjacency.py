"""
Draw data/adjacency.json, and check it against the real outlines.

adjacency.json is derived from the outlines (tools/compute_adjacency.py using
tools/outline_adjacency.py), so by default this just DRAWS it: one line, in one
colour, between the centre points of every pair of spaces the engine treats as
adjacent, with each space dotted and labelled by id. Lines crossing the
east-west seam are drawn on both sides.

    python tools/debug_adjacency.py --only sea-sea   --out exports/adjacency_sea-sea.png
    python tools/debug_adjacency.py --only land-land --out exports/adjacency_land-land.png
    python tools/debug_adjacency.py --only land-sea  --out exports/adjacency_land-sea.png

--diff colours the pairs by whether the data agrees with the outlines (useful if
adjacency.json is ever edited by hand, or the outlines change without a rebuild):

    green    adjacent in the data AND the outlines touch
    red      adjacent in the data but the outlines DO NOT touch
    magenta  outlines touch but the data has NO edge

The comparison lists are also printed and written next to the image (.json).
"""
import argparse
import json
import sys

import cv2
import numpy as np

sys.path.insert(0, __import__('os').path.dirname(__file__))
from outline_adjacency import label_image, touching_pairs  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('--touch-px', type=int, default=6, help='max gap (px) between outlines that still counts as touching')
parser.add_argument('--out', default='exports/adjacency_all.png')
parser.add_argument('--diff', action='store_true', help='colour by agreement with the outlines instead of one colour')
parser.add_argument('--only', choices=['land-land', 'land-sea', 'sea-sea'], help='draw only this kind of pair (the report still lists all)')
args = parser.parse_args()

meta = json.load(open('data/territories.json'))
spaces = {s['id']: s for s in meta['spaces']}
W, H = int(meta['reference_image_width_px']), int(meta['reference_image_height_px'])
shapes = json.load(open('data/territory_shapes.json'))['shapes']
adj = json.load(open('data/adjacency.json'))
edges = {tuple(sorted(e)) for e in adj['edges']}

# ---- what the outlines say -------------------------------------------------
labels = label_image(spaces, shapes, W, H)

# Outline problems that make the comparison below unreliable for the spaces involved:
# two spaces sharing one identical outline (their border art has a gap, so the
# extractor read them as one region), and spaces entirely hidden by another.
warnings = []
by_shape = {}
for tid in spaces:
    by_shape.setdefault(json.dumps(shapes.get(str(tid))), []).append(tid)
for group in by_shape.values():
    if len(group) > 1:
        warnings.append('identical outlines (merged region): ' + ', '.join(f"{i} {spaces[i]['name']}" for i in group))
visible = set(np.unique(labels).tolist())
for tid in spaces:
    if tid not in visible:
        warnings.append(f"{tid} {spaces[tid]['name']} is completely covered by another space's outline")

touching = touching_pairs(labels, args.touch_px)
R = args.touch_px

confirmed = sorted(edges & touching)
extra = sorted(edges - touching)
missing = sorted(touching - edges)


def kind(e):
    a, b = spaces[e[0]]['type'], spaces[e[1]]['type']
    return 'land-land' if a == b == 'land' else 'sea-sea' if a == b == 'sea' else 'land-sea'


def name(i):
    return f"{i} {spaces[i]['name']}"


# ---- the picture ---------------------------------------------------------------
img = np.full((H, W, 3), 40, np.uint8)
for kind_ in ('sea', 'land'):
    for tid, s in spaces.items():
        if s['type'] != kind_:
            continue
        col = (95, 85, 70) if kind_ == 'sea' else (150, 150, 150)
        for poly in shapes.get(str(tid), []):
            cv2.fillPoly(img, [np.array(poly, np.int32).reshape(-1, 1, 2)], col)
            cv2.polylines(img, [np.array(poly, np.int32).reshape(-1, 1, 2)], True, (30, 30, 30), 2)

GREEN, RED, MAGENTA = (60, 200, 60), (40, 40, 255), (255, 60, 255)
LINE = (60, 200, 60)  # the one colour of the default view (green)


def draw_edge(e, colour):
    a, b = spaces[e[0]], spaces[e[1]]
    p, q = np.array([a['x'], a['y']]), np.array([b['x'], b['y']])
    if q[0] - p[0] > W / 2:
        q = q - [W, 0]
    elif p[0] - q[0] > W / 2:
        q = q + [W, 0]
    width = 3
    for shift in (-W, 0, W):  # the same line at each wrap offset, so seam crossers show at both edges
        cv2.line(img, tuple((p + [shift, 0]).astype(int)), tuple((q + [shift, 0]).astype(int)), colour, width, cv2.LINE_AA)


def wanted(edges_):
    return [e for e in edges_ if args.only is None or kind(e) == args.only]


if args.diff:
    for e in wanted(confirmed):
        draw_edge(e, GREEN)
    for e in wanted(missing):
        draw_edge(e, MAGENTA)
    for e in wanted(extra):
        draw_edge(e, RED)  # last, so the suspect ones sit on top
else:
    for e in wanted(sorted(edges)):
        draw_edge(e, LINE)

for tid, s in spaces.items():
    c = (int(s['x']), int(s['y']))
    cv2.circle(img, c, 6, (0, 0, 0), -1)
    cv2.circle(img, c, 4, (255, 255, 255) if s['type'] == 'land' else (255, 200, 90), -1)
    cv2.putText(img, str(tid), (c[0] + 7, c[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, str(tid), (c[0] + 7, c[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

cv2.imwrite(args.out, img)

report = {
    'touch_px': R,
    'warnings': warnings,
    'counts': {'edges': len(edges), 'confirmed': len(confirmed), 'extra': len(extra), 'missing': len(missing)},
    'extra': [{'a': name(a), 'b': name(b), 'kind': kind((a, b))} for a, b in extra],
    'missing': [{'a': name(a), 'b': name(b), 'kind': kind((a, b))} for a, b in missing],
}
json.dump(report, open(args.out.rsplit('.', 1)[0] + '.json', 'w'), indent=1)
print(f"wrote {args.out}: {len(edges)} edges -- {len(confirmed)} confirmed by the outlines, "
      f"{len(extra)} adjacent-in-data-but-not-touching, {len(missing)} touching-but-missing-from-data")
for w in warnings:
    print('WARNING:', w)
for label, items in (('EXTRA (in data, outlines do not touch)', extra), ('MISSING (outlines touch, no edge in data)', missing)):
    print(f'\n{label}:')
    for a, b in items:
        print(f'  [{kind((a, b))}] {name(a)}  --  {name(b)}')
