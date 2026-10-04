"""
The unit emblems without their ring, for the game's small unit badges: for each assets/icons/units/<name>.svg,
writes assets/icons/units/badge/<name>.svg -- the original artwork untouched inside it, with the ring found
(tools/unit_ring.py) masked out and the view cropped to what is left, so the vehicle fills the badge. The
mask's colours are the keywords white/black: the game recolours every hex fill (the artwork's) to white.

    python tools/write_unit_badges.py
"""
import glob
import math
import os
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_data  # noqa: E402
import unit_ring  # noqa: E402

PADDING = 0.04  # of the crop, around what is left


def main():
    src_dir = tool_data.root_path(os.path.join('assets', 'icons', 'units'))
    out_dir = os.path.join(src_dir, 'badge')
    os.makedirs(out_dir, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(src_dir, '*.svg'))):
        name = os.path.basename(path)
        img = unit_ring.raster(path)
        cx, cy, ri, ro, cov = unit_ring.find_ring(img)
        band = unit_ring.trace_band(img, cx, cy, ri, ro)
        rest = img.copy()
        rest[unit_ring.band_mask(band, cx, cy) > 0] = 0
        ys, xs = np.nonzero(rest > 0)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        side = max(x1 - x0, y1 - y0) * (1 + 2 * PADDING)
        vx, vy = (x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2
        outer = ' '.join(f'{cx + o * math.cos(a):.1f},{cy + o * math.sin(a):.1f}' for a, i, o in band[::2])
        inner = ' '.join(f'{cx + i * math.cos(a):.1f},{cy + i * math.sin(a):.1f}' for a, i, o in band[::2])
        text = open(path, encoding='utf-8').read()
        body = re.sub(r'^.*?<svg[^>]*>', '', text, count=1, flags=re.S)
        body = body[:body.rindex('</svg>')]
        # (the rasters above are in the original's 512 viewBox, so the mask and crop are too)
        out = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.1f} {vy:.1f} {side:.1f} {side:.1f}">'
               f'<defs><mask id="ring" maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">'
               f'<rect x="0" y="0" width="512" height="512" fill="white"/>'
               f'<path fill="black" fill-rule="evenodd" d="M{outer}Z M{inner}Z"/>'
               f'</mask></defs><g mask="url(#ring)">{body}</g></svg>\n')
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(out)
        print(f'{name}: ring at ({cx}, {cy}) r {ri}-{ro} masked; view {side:.0f}px of 512')


if __name__ == '__main__':
    main()
