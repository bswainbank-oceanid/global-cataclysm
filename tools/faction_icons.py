"""
Icons for factions without artwork of their own -- today just the built-in Neutral and Noncombatant
factions' plain disc -- as a few simple shapes on a unit square (-1..1, y down), from which
tools/write_faction_icons.py writes the SVG file (assets/icons/faction/neutral.svg). The playable factions'
icons are artwork (assets/icons/faction/<code>.svg). The game and tools/render_map.py both draw the SVG files.

A shape is a dict: {'circle': (cx, cy, r)}, {'polygon': [(x, y), ...]}, {'polyline': [...], 'width': w}
(an open line), or {'rect': (x0, y0, x1, y1)}; 'hole': True fills it dark (the gear's centre). Shapes are filled
white with a thin dark outline ('outline': False leaves it off); the game tints them.
"""
import math


def _points(n, r_out, r_in, start=-math.pi / 2):
    return [((r_out if k % 2 == 0 else r_in) * math.cos(start + k * math.pi / (n // 2)),
             (r_out if k % 2 == 0 else r_in) * math.sin(start + k * math.pi / (n // 2))) for k in range(n)]


ICONS = {
    # the built-in Neutral and Noncombatant factions (no artwork of their own): a plain disc
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
