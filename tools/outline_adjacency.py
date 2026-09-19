"""
Which spaces touch, according to the real outlines in data/territory_shapes.json.

The outlines are painted into a label image -- seas first, land on top, because
a sea zone's polygon is only its OUTER boundary and encloses the coastal land it
borders, so painting land last leaves each sea's visible water -- and two spaces
touch when their labels come within `touch_px` pixels of each other. Wraps
east-west (the map is a cylinder). Used by tools/compute_adjacency.py (the
source of data/adjacency.json) and tools/debug_adjacency.py.
"""
import json

import cv2
import numpy as np

DEFAULT_TOUCH_PX = 6


def label_image(spaces, shapes, width, height):
    """int16 image: each pixel is the id of the space painted there (0 = none)."""
    labels = np.zeros((height, width), np.int16)
    for kind in ('sea', 'land'):
        for tid, s in spaces.items():
            if s['type'] != kind:
                continue
            for poly in shapes.get(str(tid), []):
                cv2.fillPoly(labels, [np.array(poly, np.int32).reshape(-1, 1, 2)], tid)
    return labels


def touching_pairs(labels, touch_px=DEFAULT_TOUCH_PX):
    """{(a, b), ...} with a < b, for every two ids whose pixels are within touch_px."""
    height = labels.shape[0]
    pairs = set()
    for dy in range(0, touch_px + 1):
        for dx in range(-touch_px, touch_px + 1):
            if dy == 0 and dx <= 0:
                continue
            a = labels[:height - dy] if dy else labels
            b = np.roll(labels, -dx, axis=1)[dy:]  # wraps east-west
            m = (a != b) & (a > 0) & (b > 0)
            for x, y in set(zip(a[m].tolist(), b[m].tolist())):
                pairs.add((min(x, y), max(x, y)))
    return pairs


def load(territories_path='data/territories.json', shapes_path='data/territory_shapes.json'):
    meta = json.load(open(territories_path))
    spaces = {s['id']: s for s in meta['spaces']}
    shapes = json.load(open(shapes_path))['shapes']
    return meta, spaces, shapes
