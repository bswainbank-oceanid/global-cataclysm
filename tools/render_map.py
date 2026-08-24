import json
import cv2
import numpy as np

json_path = 'data/territories.json'
out_path = 'exports/map.png'
base_path = 'assets/base_map.png'

with open(json_path) as f:
    data = json.load(f)
spaces = data['spaces']
faction_colors_hex = {k: v['color'] for k, v in json.load(open('data/factions.json'))['factions'].items()}

def hex_to_bgr(h):
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)

FAC_BGR = {k: hex_to_bgr(v) for k, v in faction_colors_hex.items()}
SEA_DOT = (0, 140, 255)
GOLD = (0, 215, 255)
WHITE = (255, 255, 255)
NAME_TXT = (255, 255, 80)

img = cv2.imread(base_path)

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

def icon_naa(img, cx, cy, r, color):
    # compass star (4 long + 4 short spikes)
    pts = []
    for k in range(8):
        ang = -np.pi / 2 + k * np.pi / 4
        rad = r if k % 2 == 0 else r * 0.45
        pts.append([int(cx + rad * np.cos(ang)), int(cy + rad * np.sin(ang))])
    pts = np.array([pts], dtype=np.int32)
    cv2.fillPoly(img, pts, color, lineType=cv2.LINE_AA)
    cv2.polylines(img, pts, True, (0, 0, 0), 1, lineType=cv2.LINE_AA)

def icon_ue(img, cx, cy, r, color):
    # gear: ring of teeth + hollow center
    cv2.circle(img, (cx, cy), int(r * 0.85), color, -1, lineType=cv2.LINE_AA)
    for k in range(8):
        ang = k * np.pi / 4
        tx = int(cx + r * 1.05 * np.cos(ang))
        ty = int(cy + r * 1.05 * np.sin(ang))
        cv2.circle(img, (tx, ty), max(1, int(r * 0.22)), color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (cx, cy), max(1, int(r * 0.35)), (20, 20, 20), -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (cx, cy), int(r * 0.85), (0, 0, 0), 1, lineType=cv2.LINE_AA)

def icon_uer(img, cx, cy, r, color):
    draw_star(img, cx, cy, r, r * 0.42, color)

def icon_gpc(img, cx, cy, r, color):
    # rising sun over a wave
    cv2.circle(img, (cx, cy - int(r * 0.15)), int(r * 0.6), color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (cx, cy - int(r * 0.15)), int(r * 0.6), (0, 0, 0), 1, lineType=cv2.LINE_AA)
    pts = np.array([[
        [int(cx - r), int(cy + r * 0.55)],
        [int(cx - r * 0.4), int(cy + r * 0.15)],
        [int(cx), int(cy + r * 0.55)],
        [int(cx + r * 0.4), int(cy + r * 0.15)],
        [int(cx + r), int(cy + r * 0.55)],
    ]], dtype=np.int32)
    cv2.polylines(img, pts, False, color, max(1, int(r * 0.18)), lineType=cv2.LINE_AA)

def icon_paf(img, cx, cy, r, color):
    # baobab: round canopy + short trunk
    cv2.circle(img, (cx, cy - int(r * 0.25)), int(r * 0.7), color, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (cx, cy - int(r * 0.25)), int(r * 0.7), (0, 0, 0), 1, lineType=cv2.LINE_AA)
    cv2.rectangle(img, (cx - max(1, int(r * 0.12)), cy), (cx + max(1, int(r * 0.12)), cy + int(r * 0.7)), color, -1)

def icon_aac(img, cx, cy, r, color):
    # condor: simple chevron wings + body dot
    pts = np.array([[
        [int(cx - r), int(cy + r * 0.1)],
        [int(cx - r * 0.25), int(cy - r * 0.35)],
        [int(cx), int(cy)],
        [int(cx + r * 0.25), int(cy - r * 0.35)],
        [int(cx + r), int(cy + r * 0.1)],
        [int(cx + r * 0.25), int(cy + r * 0.05)],
        [int(cx), int(cy + r * 0.35)],
        [int(cx - r * 0.25), int(cy + r * 0.05)],
    ]], dtype=np.int32)
    cv2.fillPoly(img, pts, color, lineType=cv2.LINE_AA)
    cv2.polylines(img, pts, True, (0, 0, 0), 1, lineType=cv2.LINE_AA)

ICON_FN = {
    'NAA': icon_naa, 'UE': icon_ue, 'UER': icon_uer,
    'GPC': icon_gpc, 'PAF': icon_paf, 'AAC': icon_aac,
}

for sp in spaces:
    x, y = int(sp['x']), int(sp['y'])
    stype = sp.get('type', 'land')
    name = sp.get('name', '')
    sid = sp.get('id', '')
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = 1

    if stype == 'sea':
        cv2.circle(img, (x, y), 4, SEA_DOT, -1, lineType=cv2.LINE_AA)
        id_text = str(sid)
        font_scale_id = 0.5
        (tw, th), baseline = cv2.getTextSize(id_text, font, font_scale_id, thickness)
        pad_x, pad_y = 5, 4
        box_w = tw + pad_x * 2
        box_h = th + pad_y * 2
        x0 = x - box_w // 2
        y1 = y - 8
        y0 = y1 - box_h
        x1 = x0 + box_w
        box_layer = img.copy()
        cv2.rectangle(box_layer, (x0, y0), (x1, y1), (0, 0, 0), -1)
        img = cv2.addWeighted(box_layer, 0.75, img, 0.25, 0)
        cv2.rectangle(img, (x0, y0), (x1, y1), WHITE, 1, lineType=cv2.LINE_AA)
        text_x = x0 + pad_x
        text_y = y1 - pad_y - baseline // 2
        cv2.putText(img, id_text, (text_x, text_y), font, font_scale_id, WHITE, thickness, lineType=cv2.LINE_AA)
        continue

    # --- land space ---
    if 'faction' not in sp:
        # unassigned territory (newly added, faction TBD)
        neutral = (110, 110, 110)
        cv2.circle(img, (x, y), 4, neutral, -1, lineType=cv2.LINE_AA)
        cv2.circle(img, (x, y), 4, (0, 0, 0), 1, lineType=cv2.LINE_AA)
        id_text = f"{sid}  --"
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
        if name:
            font_scale_name = 0.42
            (ntw, nth), nbaseline = cv2.getTextSize(name, font, font_scale_name, thickness)
            npad_x, npad_y = 4, 3
            nbox_w = ntw + npad_x * 2
            nbox_h = nth + npad_y * 2
            nx0 = x - nbox_w // 2
            ny0 = y1
            nx1 = nx0 + nbox_w
            ny1 = ny0 + nbox_h
            box_layer2 = img.copy()
            cv2.rectangle(box_layer2, (nx0, ny0), (nx1, ny1), (0, 0, 0), -1)
            img = cv2.addWeighted(box_layer2, 0.65, img, 0.35, 0)
            cv2.rectangle(img, (nx0, ny0), (nx1, ny1), (200, 200, 200), 1, lineType=cv2.LINE_AA)
            cv2.putText(img, name, (nx0 + npad_x, ny1 - npad_y - nbaseline // 2), font, font_scale_name, NAME_TXT, thickness, lineType=cv2.LINE_AA)
        continue

    fac = sp['faction']
    value = sp['value']
    is_sc = sp.get('strategic_center', False)
    is_distant = sp.get('distant', False)
    accent = FAC_BGR[fac]

    cv2.circle(img, (x, y), 4, accent, -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (x, y), 4, (0, 0, 0), 1, lineType=cv2.LINE_AA)

    # top box: "[icon] id  FAC value(+2 if SC)"
    id_text = str(sid)
    fac_text = fac
    val_text = f"{value}+2" if is_sc else str(value)
    font_scale_id = 0.5
    font_scale_fac = 0.44
    (tw_id, th_id), base_id = cv2.getTextSize(id_text, font, font_scale_id, thickness)
    (tw_fac, th_fac), base_fac = cv2.getTextSize(fac_text, font, font_scale_fac, thickness)
    (tw_val, th_val), base_val = cv2.getTextSize(val_text, font, font_scale_id, thickness)

    icon_d = 26  # icon diameter reserved at the left of the box
    gap = 5
    pad_x, pad_y = 6, 4
    content_w = icon_d + gap + tw_id + gap + tw_fac + gap + tw_val
    box_h = max(th_id, th_fac, th_val, icon_d) + pad_y * 2 + 3
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
    ICON_FN[fac](img, icon_cx, icon_cy, icon_d // 2, icon_color)

    cursor_x = x0 + pad_x + icon_d + gap
    base_y = y1 - pad_y
    cv2.putText(img, id_text, (cursor_x, base_y), font, font_scale_id, WHITE, thickness, lineType=cv2.LINE_AA)
    cursor_x += tw_id + gap
    cv2.putText(img, fac_text, (cursor_x, base_y), font, font_scale_fac, (255, 255, 255), thickness, lineType=cv2.LINE_AA)
    cursor_x += tw_fac + gap
    val_color = GOLD if is_sc else (150, 255, 150)
    cv2.putText(img, val_text, (cursor_x, base_y), font, font_scale_id, val_color, thickness, lineType=cv2.LINE_AA)

    if is_sc:
        draw_star(img, x0 - 2, y0 - 2, 7, 3, GOLD)

    if is_distant:
        cv2.circle(img, (x, y), 8, accent, 1, lineType=cv2.LINE_AA)

    # name box below
    if name:
        font_scale_name = 0.42
        (ntw, nth), nbaseline = cv2.getTextSize(name, font, font_scale_name, thickness)
        npad_x, npad_y = 4, 3
        nbox_w = ntw + npad_x * 2
        nbox_h = nth + npad_y * 2
        nx0 = x - nbox_w // 2
        ny0 = y1
        nx1 = nx0 + nbox_w
        ny1 = ny0 + nbox_h

        box_layer2 = img.copy()
        cv2.rectangle(box_layer2, (nx0, ny0), (nx1, ny1), (0, 0, 0), -1)
        img = cv2.addWeighted(box_layer2, 0.65, img, 0.35, 0)
        cv2.rectangle(img, (nx0, ny0), (nx1, ny1), accent, 1, lineType=cv2.LINE_AA)
        ntext_x = nx0 + npad_x
        ntext_y = ny1 - npad_y - nbaseline // 2
        cv2.putText(img, name, (ntext_x, ntext_y), font, font_scale_name, NAME_TXT, thickness, lineType=cv2.LINE_AA)

cv2.imwrite(out_path, img)
print('done', out_path, img.shape)
