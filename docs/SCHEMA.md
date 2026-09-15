# Data schema

All canonical game data lives under `data/` as JSON. Everything under
`derived/` and `exports/` is regenerated from `data/` by the scripts in
`tools/` — never hand-edit those two directories.

## data/territories.json

```
{
  "reference_image_width_px": 3500,
  "reference_image_height_px": 2000,
  "note": "...",
  "spaces": [
    {
      "id": int,                    // unique across land AND sea
      "type": "land" | "sea",
      "name": string,
      "x": int, "y": int,           // center point, in reference-image pixels
      "area_px": int, "bbox_w": int, "bbox_h": int,
      "faction": string | null,     // land only; one of the 6 faction codes, or null if unassigned
      "value": int,                 // land only; IPC value, 0-5
      "strategic_center": bool      // land only
    },
    ...
  ]
}
```

148 spaces total: 87 land, 61 sea. Faction reference data (name, color,
major countries, doctrine focus) lives separately in `data/factions.json`,
keyed by the same faction codes used here.

## data/adjacency.json

The Delaunay-triangulation adjacency graph — fully regenerable from
`data/territories.json` via `tools/compute_adjacency.py`, kept in `data/`
rather than `derived/` because it's foundational/canonical enough to want
reviewed directly (nothing in it is hand-edited, though). The map wraps
east-west (x=3500 is the same seam as x=0; north-south does not wrap), so
the triangulation is computed on a cylinder using the standard
ghost-point technique — every point is triangulated alongside copies of
the whole point set shifted by ±the map width, and only edges touching a
real point are kept, at the same 420px threshold used everywhere else.

```
{
  "reference_image_width_px": 3500,
  "wraps_east_west": true,
  "node_count": 148, "edge_count": 408,
  "nodes": { "<id>": {"type": "land"|"sea", "name": string}, ... },
  "edges": [[a, b], ...],                    // sorted, deduped, order-independent
  "neighbors_ordered": { "<id>": [neighbor ids...], ... }  // sorted by id; order carries no meaning
}
```

Every space in `territories.json` is a real graph node — there is no
fallback category for territories added later; the graph just includes
them next time `tools/compute_adjacency.py` runs. (Earlier revisions of
this project treated the graph as a frozen historical artifact migrated
once from a pre-project pickle, with newly-added territories approximated
by a distance-threshold fallback instead of a real triangulation. That's
gone: the graph is now a normal pipeline step like everything else under
`data/`/`derived/`.)

A coastal territory's *default* sea zone is nearest-by-distance among its
sea-type neighbors, not "first neighbor in some order" — see
`tools/compute_faction_profile.py`. (An even earlier revision used "first
sea-type neighbor in the original Delaunay build order," which was a
geometric accident, not a principled rule, and picked wrong defaults for
about half of all coastal territories. Any scenario file needs
regenerating, or at least hand-checking, after a change to this rule —
it's not simply a rendering concern.)

## data/units.json

Full stat blocks for the 8 purchasable units plus Transport (not
purchasable). See the file itself — every field is self-explanatory
(`cost`/`sc_cost` in IPC, `attack_die` as "D6".."D12", etc).

## data/factions.json

Faction reference: full name, hex color (with leading `#`), major
countries, and `focus` — the design doc's doctrine priority list driving
that faction's starting unit composition.

## data/rules.json

Every constant and formula previously hardcoded (inconsistently) across
ad hoc scripts: setup rules (budget, stacking cap formula, naval/carrier/
infantry/leftover-budget rules), combat rules, the promotion mechanic, and
map/adjacency notes. This is the single source of truth for game-balance
constants — a future game engine should read this file rather than
re-deriving these numbers.

## data/scenarios/starting_setup_200ipc.json

The 200-IPC starting-setup scenario: which units each faction buys at
each territory, which 3 units per faction get promoted, the carrier/
escort assignments, and any naval sea-zone assignments that override the
default (used to resolve collisions where two factions would otherwise
share a sea zone).

```
{
  "budget_ipc": 200,
  "purchases": {
    "<FAC>": [
      {"territory_id": int, "units": [{"unit": string, "qty": int}, ...]},
      ...
    ]
  },
  "promotions": {
    "<FAC>": [{"territory_id": int, "unit": string}, ...]   // exactly 3 per faction
  },
  "carrier_escorts": {
    "<FAC>": [{"carrier_tid": int, "aircraft_tid": int, "unit": string, "qty": int}, ...]
  },
  "naval_deploy_overrides": {
    "<FAC>": { "<territory_id>": { "<unit>": "<sea zone name>" }, ... }
  }
}
```

## derived/adjacency_foreign.json (regenerated)

For every land territory: its land-neighbor ids (`adj`), and the subset
belonging to a different faction (`foreign`). Built by
`tools/compute_foreign_neighbors.py`.

## derived/faction_territory_profile.json (regenerated)

For each faction, its owned land territories with the fields the
setup-design tools need: stacking cap, coastal flag, default sea zone,
and whether it has a foreign neighbor. Built by
`tools/compute_faction_profile.py`.

## exports/

`GC1972_Territories.xlsx` (the human-facing editing/reference workbook)
and `map.png` (the rendered faction map). Both are pure build products —
see `docs/PIPELINE.md`.
