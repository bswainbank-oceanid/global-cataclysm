"""
Shared land/sea/border pixel classification and island-chain handling for
assets/base_map.png. Used by both tools/render_map.py (color fill) and
tools/extract_territory_shapes.py (vector polygons for the eventual game
client). Kept in one place deliberately: those two consumers must agree on
exactly which pixels belong to which territory, or the rendered preview
map and the game's actual clickable shapes will silently disagree.
"""
import numpy as np
from scipy import ndimage

# (x0, y0, x1, y1) search box, in reference-image pixels. Island-chain
# territories where a single seed point only reaches one landmass in the
# chain -- every OTHER land component (not already claimed as some other
# territory's own seed) whose centroid falls in this faction gets pulled
# in too. See territory_labels().
MULTI_SEED_BOX = {
    81: (140, 940, 360, 1080),      # Cuba -- main island + Isle of Youth
    144: (340, 1600, 540, 1740),    # Falkland Islands
    101: (2170, 1150, 2410, 1370),  # Philippines
    109: (2370, 1140, 2660, 1460),  # New Guinea
    77: (2820, 900, 3020, 1040),    # Hawaii
    148: (2930, 1270, 3160, 1420),  # Polynesia
}


def classify_land_mask(img):
    """img: a cv2-loaded BGR image (e.g. assets/base_map.png). Returns a
    boolean mask, true for pixels that are neither sea-colored (teal:
    green/blue channels well above red) nor a drawn border line (near-
    black), whatever color/texture the terrain art itself actually uses.
    This is a positive test for sea + border with land as "neither",
    rather than a positive "must be brownish" test, because some terrain
    (e.g. desert/flat regions) renders near-grayscale rather than brown."""
    b_ch, g_ch, r_ch = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    sea_color_mask = (g_ch > r_ch + 20) & (b_ch > r_ch + 10)
    border_mask = (r_ch + g_ch + b_ch) < 150
    return ~sea_color_mask & ~border_mask


def label_land(img):
    """Returns (labels, num_labels, centroid_of) for the 4-connected
    components of classify_land_mask(img). centroid_of maps label -> (x, y)."""
    land_mask = classify_land_mask(img)
    labels, num_labels = ndimage.label(land_mask, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    label_ids = np.arange(1, num_labels + 1)
    centroids = ndimage.center_of_mass(land_mask, labels, label_ids) if num_labels else []
    centroid_of = {lab: (cx, cy) for lab, (cy, cx) in zip(label_ids, centroids)}
    return labels, num_labels, centroid_of


def territory_labels(spaces, labels, centroid_of):
    """Given the full `spaces` list from territories.json and a labeled
    land mask (from label_land), returns {territory_id: set(labels)} --
    which connected component(s) belong to each land territory, applying
    MULTI_SEED_BOX for island chains. Includes every land territory
    regardless of current faction assignment (shape data shouldn't
    depend on who currently owns it) -- callers that only care about
    owned territories (e.g. color fill) filter the result themselves.
    A territory whose stored (x, y) doesn't land on a mapped pixel (label
    0) is silently omitted; callers should treat that as worth a look,
    not a crash -- it's happened before (Cuba, Polynesia) when a seed
    point drifted off solid ground."""
    land_spaces = [sp for sp in spaces if sp.get('type') == 'land']

    primary_label = {}
    for sp in land_spaces:
        lab = labels[int(sp['y']), int(sp['x'])]
        if lab != 0:
            primary_label[sp['id']] = lab
    claimed = set(primary_label.values())

    result = {}
    for sp in land_spaces:
        tid = sp['id']
        own_lab = primary_label.get(tid)
        if own_lab is None:
            continue
        labs = {own_lab}
        box = MULTI_SEED_BOX.get(tid)
        if box:
            x0, y0, x1, y1 = box
            for lab, (cx, cy) in centroid_of.items():
                if lab == own_lab or lab in claimed:
                    continue
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    labs.add(lab)
        result[tid] = labs
    return result
