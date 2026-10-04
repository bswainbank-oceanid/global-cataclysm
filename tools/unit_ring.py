"""Finds the ring around a unit emblem (assets/icons/units/*.svg): rasterises it (svg_icon) and searches for the
circle -- centre and radius -- whose band is most filled, then its thickness."""
import math
import numpy as np
import cv2
import svg_icon

SIZE = 512


def raster(path):
    (vx, vy, vw, vh), shapes = svg_icon.load(path)
    img = np.zeros((SIZE, SIZE), np.uint8)
    k = SIZE / max(vw, vh)
    for sh in shapes:
        polys = [np.array([[int(round((x - vx) * k)), int(round((y - vy) * k))] for x, y in c], np.int32)
                 for c in sh['contours'] if len(c) > 2]
        if polys:
            cv2.fillPoly(img, polys, 255)
    return img


def _coverage(img, cx, cy, r, n=360):
    hits = 0
    for t in range(n):
        a = 2 * math.pi * t / n
        x, y = int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))
        if 0 <= x < SIZE and 0 <= y < SIZE and img[y, x]:
            hits += 1
    return hits / n


def find_ring(img):
    """(cx, cy, r_inner, r_outer, coverage) of the best ring, by a coarse then a fine search."""
    best = (0, 0, 0, 0)
    for cx in range(216, 297, 8):
        for cy in range(216, 297, 8):
            for r in range(150, 250, 4):
                c = _coverage(img, cx, cy, r, 120)
                if c > best[0]:
                    best = (c, cx, cy, r)
    _, cx0, cy0, r0 = best
    for cx in range(cx0 - 6, cx0 + 7, 2):
        for cy in range(cy0 - 6, cy0 + 7, 2):
            for r in range(r0 - 5, r0 + 6):
                c = _coverage(img, cx, cy, r)
                if c > best[0]:
                    best = (c, cx, cy, r)
    cov, cx, cy, r = best
    # the band: radii around r still about as covered as r itself
    inner = r
    while inner > r - 40 and _coverage(img, cx, cy, inner - 1) > 0.6 * cov:
        inner -= 1
    outer = r
    while outer < r + 40 and _coverage(img, cx, cy, outer + 1) > 0.6 * cov:
        outer += 1
    return cx, cy, inner, outer, cov


def trace_band(img, cx, cy, ri, ro, steps=720, reach=24, margin=4):
    """[(angle, inner, outer)] -- the ring's actual edges all round (it is seldom a perfect circle): at each
    angle, the filled run nearest the found radius no thicker than the ring (a run where the vehicle crosses
    it, or a gap, takes the neighbouring angle's band)."""
    mid, thick = (ri + ro) / 2, ro - ri
    band, prev = [], (ri - margin, ro + margin)
    for t in range(steps):
        a = 2 * math.pi * t / steps
        ca, sa = math.cos(a), math.sin(a)
        runs, start = [], None
        for r in range(int(mid - reach), int(mid + reach) + 1):
            x, y = int(round(cx + r * ca)), int(round(cy + r * sa))
            on = 0 <= x < SIZE and 0 <= y < SIZE and img[y, x] > 0
            if on and start is None:
                start = r
            elif not on and start is not None:
                runs.append((start, r - 1))
                start = None
        if start is not None:
            runs.append((start, int(mid + reach)))
        fits = [(abs((s + e) / 2 - mid), s, e) for s, e in runs if 0.5 * thick <= e - s + 1 <= 1.8 * thick + 2]
        if fits:
            _, s, e = min(fits)
            prev = (s - margin, e + margin)
        band.append((a, prev[0], prev[1]))
    return band


def band_mask(band, cx, cy, shape=(SIZE, SIZE)):
    """The traced band as a filled raster mask (255 = ring)."""
    outer = np.array([[cx + o * math.cos(a), cy + o * math.sin(a)] for a, i, o in band], np.int32)
    inner = np.array([[cx + i * math.cos(a), cy + i * math.sin(a)] for a, i, o in band], np.int32)
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [outer], 255)
    cv2.fillPoly(m, [inner], 0)
    return m
