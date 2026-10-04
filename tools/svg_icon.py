"""
Reads a simple icon SVG -- <path> (M/m, L/l, H/h, V/v, C/c, Z/z), <polygon> and <circle> elements, under <g> groups with
translate/scale transforms, as traced icons are -- into polygons, so tools can draw it with OpenCV without an
SVG library. Curves are flattened into short segments. Coordinates come back in the SVG's viewBox space.

    shapes = load('assets/icons/faction/naa.svg')   # [{'contours': [[(x, y), ...], ...], 'fill': '#fff', 'stroke': ...}]
    view = view_box(...)
"""
import math
import re
import xml.etree.ElementTree as ET

_CURVE_STEPS = 8
_NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_TOKEN = re.compile(r'[MmLlHhVvCcZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


def _transform(text):
    """A 'translate(...) scale(...)' list as (a, d, e, f): x' = a*x + e, y' = d*y + f (no rotation/skew)."""
    a, d, e, f = 1.0, 1.0, 0.0, 0.0
    for name, args in re.findall(r'(translate|scale)\s*\(([^)]*)\)', text or ''):
        nums = [float(n) for n in _NUM.findall(args)]
        if name == 'translate':
            tx, ty = nums[0], nums[1] if len(nums) > 1 else 0.0
            e, f = e + a * tx, f + d * ty
        else:
            sx, sy = nums[0], nums[1] if len(nums) > 1 else nums[0]
            a, d = a * sx, d * sy
    return a, d, e, f


def _compose(outer, inner):
    a1, d1, e1, f1 = outer
    a2, d2, e2, f2 = inner
    return a1 * a2, d1 * d2, a1 * e2 + e1, d1 * f2 + f1


def _path_contours(d):
    """The subpaths of path data `d` as lists of points (curves flattened)."""
    tokens = _TOKEN.findall(d)
    contours, current = [], []
    x = y = sx = sy = 0.0
    cmd, i = None, 0

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in 'Zz':
                if current:
                    contours.append(current)
                current = []
                x, y = sx, sy
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == 'M':
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            if current:
                contours.append(current)
            current, sx, sy = [(x, y)], x, y
            cmd = 'l' if rel else 'L'  # further pairs are line-tos
        elif c == 'L':
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            current.append((x, y))
        elif c == 'H':
            nx = num()
            x = x + nx if rel else nx
            current.append((x, y))
        elif c == 'V':
            ny = num()
            y = y + ny if rel else ny
            current.append((x, y))
        elif c == 'C':
            pts = [num() for _ in range(6)]
            if rel:
                pts = [pts[k] + (x if k % 2 == 0 else y) for k in range(6)]
            x1, y1, x2, y2, x3, y3 = pts
            for s in range(1, _CURVE_STEPS + 1):
                t = s / _CURVE_STEPS
                mt = 1 - t
                current.append((mt ** 3 * x + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t ** 3 * x3,
                                mt ** 3 * y + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t ** 3 * y3))
            x, y = x3, y3
        else:
            raise ValueError(f'unsupported path command {cmd!r}')
    if current:
        contours.append(current)
    return contours


def _circle(cx, cy, r, steps=32):
    return [(cx + r * math.cos(2 * math.pi * k / steps), cy + r * math.sin(2 * math.pi * k / steps)) for k in range(steps)]


def load(path):
    """(view box (x, y, w, h), shapes): every <path>/<circle> as {'contours', 'fill', 'stroke', 'stroke_width'} in
    viewBox coordinates. Fill and stroke are inherited from enclosing groups."""
    root = ET.parse(path).getroot()
    view = tuple(float(v) for v in _NUM.findall(root.get('viewBox', '0 0 512 512')))
    shapes = []

    def walk(el, tf, style):
        tag = el.tag.rsplit('}', 1)[-1]
        style = dict(style)
        for key in ('fill', 'stroke', 'stroke-width'):
            if el.get(key) is not None:
                style[key] = el.get(key)
        tf = _compose(tf, _transform(el.get('transform')))
        a, d, e, f = tf
        if tag == 'path':
            contours = [[(a * px + e, d * py + f) for px, py in c] for c in _path_contours(el.get('d', ''))]
        elif tag in ('polygon', 'polyline'):
            nums = [float(n) for n in _NUM.findall(el.get('points', ''))]
            contours = [[(a * nums[k] + e, d * nums[k + 1] + f) for k in range(0, len(nums) - 1, 2)]]
        elif tag == 'circle':
            c = _circle(float(el.get('cx', 0)), float(el.get('cy', 0)), float(el.get('r', 0)))
            contours = [[(a * px + e, d * py + f) for px, py in c]]
        else:
            contours = None
        if contours:
            shapes.append({'contours': contours, 'fill': style.get('fill', '#000'), 'stroke': style.get('stroke'),
                           'stroke_width': float(style.get('stroke-width', 0) or 0) * abs(a)})
        for child in el:
            walk(child, tf, style)

    walk(root, (1.0, 1.0, 0.0, 0.0), {})
    return view, shapes
