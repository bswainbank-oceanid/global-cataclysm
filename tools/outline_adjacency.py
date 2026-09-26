"""
Which spaces touch, according to the real outlines (the Map module's boundaries).

The outlines are painted into a label image -- seas first, land on top, because
a sea zone's polygon is only its OUTER boundary and encloses the coastal land it
borders, so painting land last leaves each sea's visible water -- and two spaces
touch when their labels come within `touch_px` pixels of each other. Wraps
east-west on a cylinder map. Used by tools/compute_adjacency.py (the source of
the Map's adjacency) and tools/debug_adjacency.py.
"""
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


def touching_pairs(labels, touch_px=DEFAULT_TOUCH_PX, wraps=True):
    """{(a, b), ...} with a < b, for every two ids whose pixels are within touch_px (across the
    east-west seam too when `wraps`)."""
    height, width = labels.shape
    pairs = set()
    for dy in range(0, touch_px + 1):
        for dx in range(-touch_px, touch_px + 1):
            if dy == 0 and dx <= 0:
                continue
            a = labels[:height - dy] if dy else labels
            b = np.roll(labels, -dx, axis=1)[dy:]  # wraps east-west
            m = (a != b) & (a > 0) & (b > 0)
            if not wraps and dx:
                # a flat map: drop the columns np.roll carried across the seam
                seam = np.zeros(width, bool)
                seam[:max(0, -dx)] = True
                seam[width - max(0, dx):] = True
                m &= ~seam[np.newaxis, :]
            for x, y in set(zip(a[m].tolist(), b[m].tolist())):
                pairs.add((min(x, y), max(x, y)))
    return pairs


def load():
    """(map meta, {id: space}, {str(id): polygons}) for the scenario in use (tool_data.use_scenario)."""
    import tool_data
    meta = dict(tool_data.map_meta(), spaces=tool_data.spaces())
    spaces = {s['id']: s for s in meta['spaces']}
    shapes = tool_data.shapes()
    return meta, spaces, shapes
