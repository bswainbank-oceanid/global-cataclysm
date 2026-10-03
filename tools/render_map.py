import argparse
import cv2
import numpy as np
import tool_data
from map_geometry import label_land, territory_labels
import faction_icons

parser = argparse.ArgumentParser()
parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
parser.add_argument('--base', default=None, help="source base map image (default: the map's own image)")
parser.add_argument('--out', default='exports/map.png', help='output rendered map path')
args = parser.parse_args()

tool_data.use_scenario(args.scenario)
out_path = args.out
base_path = args.base or tool_data.root_path(tool_data.map_meta()['image'])

spaces = tool_data.spaces()
faction_colors_hex = {k: v['color'] for k, v in tool_data.factions().items()}
# the built-in Neutral and Noncombatant factions, for land the faction assignment gives them
for builtin in (tool_data.config().neutral_faction(), tool_data.config().noncombatant_faction()):
    faction_colors_hex.setdefault(builtin['id'], builtin['color'])
# each faction's icon (the faction set's `icon`: assets/icons/factions/<name>.svg, drawn from tools/faction_icons.py)
FACTION_ICONS = {k: v.get('icon') for k, v in tool_data.factions().items()}
_fset = tool_data.config().faction_set
for _key in ('neutral', 'noncombatant'):
    if _fset.get(_key):
        FACTION_ICONS[_fset[_key].get('id')] = _fset[_key].get('icon')

def hex_to_bgr(h):
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)

FAC_BGR = {k: hex_to_bgr(v) for k, v in faction_colors_hex.items()}
SEA_DOT = (0, 140, 255)
GOLD = (0, 215, 255)
WHITE = (255, 255, 255)
NAME_TXT = (255, 255, 80)
LABEL_BG = (235, 235, 235)
LABEL_BORDER = (60, 60, 60)
BLACK = (0, 0, 0)

img = cv2.imread(base_path)

# ---------------------------------------------------------------------
# Faction-colored land fill: flood-fill each land territory's region
# (bounded by the base map's drawn black border lines) starting from its
# center point, then blend in the controlling faction's accent color.
# This is approximate, not authoritative -- a border line with a gap
# lets the fill bleed into a neighboring territory. Harmless when the
# neighbor is the same faction (same color either way); a visible bug
# when it's a different faction. Known to happen at least once (South
# Africa/Namibia) as of this writing; expect to hand-touch-up
# assets/base_map.png's border lines as more get spotted.
#
# Island-chain territories are a separate problem: a single seed point
# only fills the one landmass it sits on, leaving every other island in
# the chain uncolored (they're separate connected components -- water
# in between, not a border-line gap). MULTI_SEED_BOX gives those
# territories a search box instead of a single point: every land
# component with its centroid inside the box gets included, as long as
# it isn't already some OTHER territory's own seed component (that
# guard is what makes it safe to draw these boxes generously without
# risking swallowing a neighboring country).
# ---------------------------------------------------------------------
FILL_ALPHA = 1.0
land_labels, num_labels, centroid_of = label_land(img)
labels_by_territory = territory_labels(spaces, land_labels, centroid_of)

land_spaces = [sp for sp in spaces if sp.get('type') == 'land' and sp.get('faction')]

for sp in land_spaces:
    tid = sp['id']
    labels_to_fill = labels_by_territory.get(tid)
    if not labels_to_fill:
        continue
    region = np.isin(land_labels, list(labels_to_fill))
    color = np.array(FAC_BGR[sp['faction']], dtype=float)
    img[region] = (img[region].astype(float) * (1 - FILL_ALPHA) + color * FILL_ALPHA).astype(np.uint8)

def darken(color, factor=0.55):
    return tuple(int(c * factor) for c in color)

def lighten(color, factor=0.55):
    # blend toward white by `factor` (0 = original color, 1 = pure white)
    return tuple(int(c + (255 - c) * factor) for c in color)

def star_points(cx, cy, r_out, r_in):
    pts = []
    for k in range(10):
        ang = -np.pi / 2 + k * np.pi / 5
        r = r_out if k % 2 == 0 else r_in
        x = int(cx + r * np.cos(ang))
        y = int(cy + r * np.sin(ang))
        pts.append([x, y])
    return np.array([pts], dtype=np.int32)

def draw_star(img, cx, cy, r_out, r_in, fill_color, outline_color=(0, 0, 0)):
    pts = star_points(cx, cy, r_out, r_in)
    cv2.fillPoly(img, pts, fill_color, lineType=cv2.LINE_AA)
    cv2.polylines(img, pts, True, outline_color, 1, lineType=cv2.LINE_AA)

# ---------------------------------------------------------------------
# Faction icons, drawn at small size (~18px) directly onto the map next
# to each controlled territory's dot. Same glyph language as the faction
# reference card: NAA compass star, UE gear+rings, UER star+depth-rings,
# GPC rising sun + waves, PAF baobab, AAC condor -- simplified further
# for legibility at map scale.
# ---------------------------------------------------------------------

def draw_faction_icon(img, name, cx, cy, r, color):
    """Faction icon `name` (tools/faction_icons.py -- the same shapes as the game's SVGs), radius `r`, in `color`."""
    def px(x, y):
        return [int(cx + x * r), int(cy + y * r)]
    for s in faction_icons.ICONS[name]:
        fill = (20, 20, 20) if s.get('hole') else color
        outline = s.get('outline', True)
        if 'circle' in s:
            x, y, rr = s['circle']
            cv2.circle(img, tuple(px(x, y)), max(1, int(rr * r)), fill, -1, lineType=cv2.LINE_AA)
            if outline:
                cv2.circle(img, tuple(px(x, y)), max(1, int(rr * r)), (0, 0, 0), 1, lineType=cv2.LINE_AA)
        elif 'polygon' in s:
            pts = np.array([[px(x, y) for x, y in s['polygon']]], dtype=np.int32)
            cv2.fillPoly(img, pts, fill, lineType=cv2.LINE_AA)
            if outline:
                cv2.polylines(img, pts, True, (0, 0, 0), 1, lineType=cv2.LINE_AA)
        elif 'rect' in s:
            x0, y0, x1, y1 = s['rect']
            cv2.rectangle(img, tuple(px(x0, y0)), tuple(px(x1, y1)), fill, -1)
        else:
            pts = np.array([[px(x, y) for x, y in s['polyline']]], dtype=np.int32)
            cv2.polylines(img, pts, False, color, max(1, int(r * s['width'])), lineType=cv2.LINE_AA)


def icon_name(faction):
    """The icon a faction is drawn with: its faction-set icon's file name, else the plain one."""
    file = (FACTION_ICONS.get(faction) or '').rsplit('/', 1)[-1]
    name = file[:-4] if file.endswith('.svg') else ''
    return name if name in faction_icons.ICONS else faction_icons.DEFAULT_ICON


def draw_id_name_label(img, cx, cy, sid, name, above=False):
    """Every space gets this: plain black lettering, 'id. name', no box.
    Placed above the space's point for sea spaces, below it for land (so
    it doesn't collide with the land faction box, which sits above)."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5
    thickness = 1
    text = f"{sid}. {name}" if name else f"{sid}."
    (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    text_x = cx - tw // 2
    text_y = (cy - 8) if above else (cy + 8 + th)
    cv2.putText(img, text, (text_x, text_y), font, font_scale, BLACK, thickness, lineType=cv2.LINE_AA)
    return img

for sp in spaces:
    x, y = int(sp['x']), int(sp['y'])
    stype = sp.get('type', 'land')
    name = sp.get('name', '')
    sid = sp.get('id', '')
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = 1

    if stype == 'sea':
        img = draw_id_name_label(img, x, y, sid, name, above=True)
        continue

    # --- land space ---
    if not sp.get('faction'):
        # unassigned territory (newly added, faction TBD)
        id_text = '--'
        font_scale_id = 0.5
        (tw, th), baseline = cv2.getTextSize(id_text, font, font_scale_id, thickness)
        pad_x, pad_y = 6, 4
        box_w = tw + pad_x * 2
        box_h = th + pad_y * 2
        x0 = x - box_w // 2
        y1 = y - 8
        y0 = y1 - box_h
        x1 = x0 + box_w
        fill_layer = img.copy()
        cv2.rectangle(fill_layer, (x0, y0), (x1, y1), (40, 40, 40), -1)
        img = cv2.addWeighted(fill_layer, 0.72, img, 0.28, 0)
        cv2.rectangle(img, (x0, y0), (x1, y1), (200, 200, 200), 1, lineType=cv2.LINE_AA)
        cv2.putText(img, id_text, (x0 + pad_x, y1 - pad_y), font, font_scale_id, WHITE, thickness, lineType=cv2.LINE_AA)
        img = draw_id_name_label(img, x, y, sid, name, above=False)
        continue

    fac = sp['faction']
    value = sp['value']
    is_sc = sp.get('strategic_center', False)
    accent = FAC_BGR[fac]

    # top box: "[icon] FAC value" (value already includes the +2 SC bonus)
    fac_text = fac
    val_text = str(value + 2 if is_sc else value)
    font_scale_id = 0.5
    font_scale_fac = 0.44
    (tw_fac, th_fac), base_fac = cv2.getTextSize(fac_text, font, font_scale_fac, thickness)
    (tw_val, th_val), base_val = cv2.getTextSize(val_text, font, font_scale_id, thickness)

    icon_d = 26  # icon diameter reserved at the left of the box
    gap = 5
    pad_x, pad_y = 6, 4
    content_w = icon_d + gap + tw_fac + gap + tw_val
    box_h = max(th_fac, th_val, icon_d) + pad_y * 2 + 3
    box_w = content_w + pad_x * 2

    x0 = x - box_w // 2
    y1 = y - 8
    y0 = y1 - box_h
    x1 = x0 + box_w

    fill_layer = img.copy()
    cv2.rectangle(fill_layer, (x0, y0), (x1, y1), darken(accent, 0.5), -1)
    img = cv2.addWeighted(fill_layer, 0.72, img, 0.28, 0)

    border_color = GOLD if is_sc else WHITE
    border_thick = 2 if is_sc else 1
    cv2.rectangle(img, (x0, y0), (x1, y1), border_color, border_thick, lineType=cv2.LINE_AA)

    # faction icon, vertically centered in the box, tinted a light version
    # of the faction's accent color so icons read as color-coded against
    # the darkened-accent box background while staying legible/distinct.
    icon_cx = x0 + pad_x + icon_d // 2
    icon_cy = (y0 + y1) // 2
    icon_color = lighten(accent, 0.6)
    draw_faction_icon(img, icon_name(fac), icon_cx, icon_cy, icon_d // 2, icon_color)

    cursor_x = x0 + pad_x + icon_d + gap
    base_y = icon_cy + max(th_fac, th_val) // 2
    cv2.putText(img, fac_text, (cursor_x, base_y), font, font_scale_fac, (255, 255, 255), thickness, lineType=cv2.LINE_AA)
    cursor_x += tw_fac + gap
    val_color = GOLD if is_sc else (150, 255, 150)
    cv2.putText(img, val_text, (cursor_x, base_y), font, font_scale_id, val_color, thickness, lineType=cv2.LINE_AA)

    if is_sc:
        draw_star(img, x0 - 2, y0 - 2, 7, 3, GOLD)

    img = draw_id_name_label(img, x, y, sid, name, above=False)

cv2.imwrite(out_path, img)
print('done', out_path, img.shape)
