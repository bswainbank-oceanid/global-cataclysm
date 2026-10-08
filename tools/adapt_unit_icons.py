"""
Adapts traced unit icons (assets/icons/units3/*.svg: each a silhouette in its own colour, in a viewBox of
its own size, with the tracing's specks along the edges) into the form the game draws them in: one white
shape (the client tints it the owner's colour), centred in a 512x512 viewBox, edges cleaned, holes (a
tank's wheels) kept. Writes assets/icons/units3/adapted/<name>.svg and a contact sheet, contact_sheet.png.

    python tools/adapt_unit_icons.py [--source assets/icons/units3] [--godot PATH]

How: Godot draws each SVG large (tools/godot/raster_svgs.gd -- Python here has no SVG renderer); the
picture's shape is cleaned (specks removed, edges smoothed) and traced back into outlines (OpenCV), which
become the new SVG's one path. The originals are left as they are.
"""
import argparse
import glob
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'assets', 'icons', 'units3')
RASTER = os.path.join(ROOT, 'tools', 'godot', 'raster_svgs.gd')
CLIENT = os.path.join(ROOT, 'client')
BOX = 512        # the game's icon viewBox
FILL = 0.90      # the shape's longer side, as a share of the box
SPECK = 0.002    # a separate bit smaller than this share of the shape's area is a tracing speck
SMOOTH_PX = 3    # edge smoothing at the ~1200px drawing size
DETAIL_PX = 1.2  # outline simplification at that size (well under a pixel once drawn at 64px)


def find_godot():
    found = sorted(glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages',
                                          'GodotEngine.GodotEngine*', 'Godot_v*_win64_console.exe')))
    return found[-1] if found else None


def rasterise(godot, source, out):
    subprocess.run([godot, '--headless', '--path', CLIENT, '-s', RASTER, '--', source, out], check=True,
                   stdout=subprocess.DEVNULL)


def clean_mask(png):
    """The shape, cleaned: a 0/255 mask with the specks gone and the edges smoothed."""
    img = cv2.imread(png, cv2.IMREAD_UNCHANGED)
    alpha = img[:, :, 3] if img.ndim == 3 and img.shape[2] == 4 else 255 - cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = (alpha > 127).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SMOOTH_PX, SMOOTH_PX))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)    # specks and hairs along the edges
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)   # pinholes and nicks
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    total = stats[1:, cv2.CC_STAT_AREA].sum() if n > 1 else 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < SPECK * total:
            mask[labels == i] = 0
    mask = cv2.GaussianBlur(mask, (0, 0), SMOOTH_PX / 2)  # a softer, rounder edge before tracing
    return (mask > 127).astype(np.uint8) * 255


def svg_for(mask):
    """One white path (outlines and their holes, even-odd) centred in the BOX x BOX viewBox."""
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    scale = FILL * BOX / max(x1 - x0, y1 - y0)
    ox = (BOX - (x1 - x0) * scale) / 2 - x0 * scale
    oy = (BOX - (y1 - y0) * scale) / 2 - y0 * scale
    parts = []
    for c in contours:
        c = cv2.approxPolyDP(c, DETAIL_PX, True).reshape(-1, 2)
        if len(c) < 3:
            continue
        pts = ' '.join(f'{x * scale + ox:.1f} {y * scale + oy:.1f}' for x, y in c)
        parts.append(f'M{pts}Z')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}">'
            f'<path fill="#fff" fill-rule="evenodd" d="{" ".join(parts)}"/></svg>\n')


def contact_sheet(masks, path, cell=180):
    names = sorted(masks)
    cols = 3
    rows = (len(names) + cols - 1) // cols
    sheet = np.full((rows * cell, cols * cell, 3), (51, 36, 11), np.uint8)  # (the HUD's navy, BGR)
    for i, name in enumerate(names):
        m = masks[name]
        ys, xs = np.nonzero(m)
        crop = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        s = 0.8 * cell / max(crop.shape)
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))), interpolation=cv2.INTER_AREA)
        r, c = divmod(i, cols)
        y = r * cell + (cell - crop.shape[0]) // 2
        x = c * cell + (cell - crop.shape[1]) // 2
        roi = sheet[y:y + crop.shape[0], x:x + crop.shape[1]]
        a = (crop.astype(np.float32) / 255)[:, :, None]
        roi[:] = (roi * (1 - a) + 255 * a).astype(np.uint8)
        cv2.putText(sheet, name, (c * cell + 6, r * cell + cell - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.imwrite(path, sheet)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=SOURCE)
    parser.add_argument('--godot', default=None)
    args = parser.parse_args(argv)
    godot = args.godot or find_godot()
    if not godot:
        parser.error("can't find Godot: pass --godot")
    out = os.path.join(args.source, 'adapted')
    os.makedirs(out, exist_ok=True)
    masks = {}
    with tempfile.TemporaryDirectory() as tmp:
        rasterise(godot, os.path.abspath(args.source), tmp)
        for png in sorted(glob.glob(os.path.join(tmp, '*.png'))):
            name = os.path.splitext(os.path.basename(png))[0]
            mask = clean_mask(png)
            masks[name] = mask
            with open(os.path.join(out, name + '.svg'), 'w', encoding='utf-8') as f:
                f.write(svg_for(mask))
            print(f'{name:12s} -> {os.path.relpath(os.path.join(out, name + ".svg"), ROOT)}')
    contact_sheet(masks, os.path.join(out, 'contact_sheet.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
