"""
Debug picture (and report) for data/adjacency.json.

adjacency.json is computed from the spaces' CENTRE POINTS (a Delaunay
triangulation with a length cap), so nothing guarantees two "adjacent" spaces
actually touch. This checks it against the real outlines in
data/territory_shapes.json: the outlines are painted into a label image (seas
first, land on top -- a sea polygon is only its outer boundary, so this leaves
each sea's visible water), and two spaces "touch" if their labels come within
a few pixels of each other, wrapping east-west.

exports/adjacency_debug.png draws a line between the centre points of every
pair the engine treats as adjacent, coloured by what the outlines say:

    green    adjacent in the data AND their outlines touch
    red      adjacent in the data but the outlines DO NOT touch   (suspect extra edge)
    magenta  outlines touch but the data has NO edge             (suspect missing edge)

(line width is heavier for land<->sea pairs, the ones under suspicion). Lines
crossing the east-west seam are drawn on both sides. Spaces are dotted and
labelled with their id. The same lists are printed, and written to
exports/adjacency_debug.json.

    python tools/debug_adjacency.py [--touch-px 6] [--only land-sea] [--out exports/adjacency_debug.png]
"""
import argparse
import json

import cv2
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument('--touch-px', type=int, default=6, help='max gap (px) between outlines that still counts as touching')
parser.add_argument('--out', default='exports/adjacency_debug.png')
parser.add_argument('--only', choices=['land-land', 'land-sea', 'sea-sea'], help='draw only this kind of pair (the report still lists all)')
args = parser.parse_args()

meta = json.load(open('data/territories.json'))
spaces = {s['id']: s for s in meta['spaces']}
W, H = int(meta['reference_image_width_px']), int(meta['reference_image_height_px'])
shapes = json.load(open('data/territory_shapes.json'))['shapes']
adj = json.load(open('data/adjacency.json'))
edges = {tuple(sorted(e)) for e in adj['edges']}

# ---- what the outlines say -------------------------------------------------
labels = np.zeros((H, W), np.int16)
for kind in ('sea', 'land'):  # land painted last, on top of the sea polygons enclosing it
    for tid, s in spaces.items():
        if s['type'] != kind:
            continue
        for poly in shapes.get(str(tid), []):
            cv2.fillPoly(labels, [np.array(poly, np.int32).reshape(-1, 1, 2)], tid)

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

touching = set()
R = args.touch_px
for dy in range(0, R + 1):
    for dx in range(-R, R + 1):
        if dy == 0 and dx <= 0:
            continue
        a = labels[:H - dy] if dy else labels
        b = np.roll(labels, -dx, axis=1)[dy:]  # wraps east-west
        if dy == 0:
            a, b = labels, np.roll(labels, -dx, axis=1)
        m = (a != b) & (a > 0) & (b > 0)
        for x, y in set(zip(a[m].tolist(), b[m].tolist())):
            touching.add((min(x, y), max(x, y)))

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


def draw_edge(e, colour):
    a, b = spaces[e[0]], spaces[e[1]]
    p, q = np.array([a['x'], a['y']]), np.array([b['x'], b['y']])
    if q[0] - p[0] > W / 2:
        q = q - [W, 0]
    elif p[0] - q[0] > W / 2:
        q = q + [W, 0]
    width = 3 if kind(e) == 'land-sea' else 2
    for shift in (-W, 0, W):  # the same line at each wrap offset, so seam crossers show at both edges
        cv2.line(img, tuple((p + [shift, 0]).astype(int)), tuple((q + [shift, 0]).astype(int)), colour, width, cv2.LINE_AA)


def wanted(edges_):
    return [e for e in edges_ if args.only is None or kind(e) == args.only]


for e in wanted(confirmed):
    draw_edge(e, GREEN)
for e in wanted(missing):
    draw_edge(e, MAGENTA)
for e in wanted(extra):
    draw_edge(e, RED)  # last, so the suspect ones sit on top

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
