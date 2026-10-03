"""
The faction icons, once: each faction's glyph as a few simple shapes on a unit square (-1..1, y down),
from which tools/write_faction_icons.py writes the SVG files the game shows (assets/icons/factions/*.svg,
named by each faction's `icon` in the faction set) and tools/render_map.py draws exports/map.png -- so the
two never differ.

A shape is a dict: {'circle': (cx, cy, r)}, {'polygon': [(x, y), ...]}, {'polyline': [...], 'width': w}
(an open line), or {'rect': (x0, y0, x1, y1)}; 'hole': True fills it dark (the gear's centre). Shapes are filled
white with a thin dark outline ('outline': False leaves it off); the game tints them.
"""
import math


def _points(n, r_out, r_in, start=-math.pi / 2):
    return [((r_out if k % 2 == 0 else r_in) * math.cos(start + k * math.pi / (n // 2)),
             (r_out if k % 2 == 0 else r_in) * math.sin(start + k * math.pi / (n // 2))) for k in range(n)]


ICONS = {
    # compass star: 4 long and 4 short points
    'naa': [{'polygon': _points(8, 1.0, 0.45)}],
    # gear: a disc with 8 teeth and a hollow centre
    'ue': ([{'circle': (1.05 * math.cos(k * math.pi / 4), 1.05 * math.sin(k * math.pi / 4), 0.22), 'outline': False}
            for k in range(8)]
           + [{'circle': (0, 0, 0.85)}, {'circle': (0, 0, 0.35), 'hole': True}]),
    # five-pointed star
    'uer': [{'polygon': _points(10, 1.0, 0.42)}],
    # rising sun over a wave
    'gpc': [{'circle': (0, -0.15, 0.6)},
            {'polyline': [(-1, 0.55), (-0.4, 0.15), (0, 0.55), (0.4, 0.15), (1, 0.55)], 'width': 0.18}],
    # baobab: round canopy on a short trunk
    'paf': [{'rect': (-0.12, 0, 0.12, 0.7), 'outline': False}, {'circle': (0, -0.25, 0.7)}],
    # condor: chevron wings and a body
    'aac': [{'polygon': [(-1, 0.1), (-0.25, -0.35), (0, 0), (0.25, -0.35), (1, 0.1), (0.25, 0.05), (0, 0.35), (-0.25, 0.05)]}],
    # the built-in Neutral and Noncombatant factions: a plain disc
    'neutral': [{'circle': (0, 0, 0.7)}],
}

# faction id -> icon name, for factions whose faction-set entry names no icon (render_map's fallback)
DEFAULT_ICON = 'neutral'


def svg(name, size=512):
    """The SVG text for icon `name`: white shapes, dark outlines, on a transparent square `size` wide."""
    half = size / 2
    scale = size * 0.42  # the unit square's radius 1 -> leaves a margin for the outlines and teeth
    outline = max(1.0, size / 64)

    def pt(x, y):
        return f'{half + x * scale:.1f},{half + y * scale:.1f}'

    body = []
    for s in ICONS[name]:
        stroke = f' stroke="#141414" stroke-width="{outline:.1f}"' if s.get('outline', True) else ''
        fill = '#141414' if s.get('hole') else '#fff'
        if 'circle' in s:
            cx, cy, r = s['circle']
            body.append(f'<circle cx="{half + cx * scale:.1f}" cy="{half + cy * scale:.1f}" r="{r * scale:.1f}" '
                        f'fill="{fill}"{stroke}/>')
        elif 'polygon' in s:
            body.append('<polygon points="' + ' '.join(pt(x, y) for x, y in s['polygon']) + f'" fill="{fill}"{stroke}/>')
        elif 'rect' in s:
            x0, y0, x1, y1 = s['rect']
            body.append(f'<rect x="{half + x0 * scale:.1f}" y="{half + y0 * scale:.1f}" width="{(x1 - x0) * scale:.1f}" '
                        f'height="{(y1 - y0) * scale:.1f}" fill="{fill}"{stroke}/>')
        else:
            pts = ' '.join(pt(x, y) for x, y in s['polyline'])
            body.append(f'<polyline points="{pts}" fill="none" stroke="#fff" stroke-width="{s["width"] * scale:.1f}" '
                        f'stroke-linejoin="round" stroke-linecap="round"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{"".join(body)}</svg>' + '\n'
