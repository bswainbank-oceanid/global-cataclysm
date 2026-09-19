# Regeneration pipeline

Everything under `derived/` and `exports/` is generated from `data/` +
`tools/`. Run the whole thing with:

```
python3 tools/build_all.py
```

which runs, in order:

1. `tools/compute_adjacency.py` — `data/territory_shapes.json` (so
   `tools/extract_territory_shapes.py` runs first) → `data/adjacency.json`;
   two spaces are adjacent when their real outlines touch (within a few
   pixels, wrapping east-west, a sea zone counting as its visible water
   only -- `tools/outline_adjacency.py`). This replaced a centre-point
   Delaunay triangulation that got ~120 pairs wrong and missed ~125. Two
   land spaces separated by water (England/Benelux) are therefore not
   adjacent; they connect through the sea between. Fully regenerable —
   kept in `data/` rather than `derived/` because it's
   foundational/canonical enough to want reviewed directly, but nothing
   in the generated file is hand-edited; the few hand corrections live in
   `data/adjacency_overrides.json` (`remove`: outlines touch but the pair is not
   adjacent in play; `add`: adjacent in play but the outlines don't touch), and
   the script warns about any that no longer have an effect.
2. `tools/compute_foreign_neighbors.py` — needs (1); `data/territories.json`
   + `data/adjacency.json` → `derived/adjacency_foreign.json`
3. `tools/compute_faction_profile.py` — needs (2) →
   `derived/faction_territory_profile.json`
4. `tools/build_master_xlsx.py` — `data/territories.json` +
   `data/factions.json` → `exports/GC1972_Territories.xlsx` ('All
   Territories' tab + 6 faction subtabs + Unassigned)
5. `tools/build_setup_tab.py` — needs (3) and (4); adds/replaces the
   'Initial Setup' tab on the same workbook from
   `data/scenarios/starting_setup_200ipc.json` + `data/units.json`
6. `/mnt/skills/public/xlsx/scripts/recalc.py exports/GC1972_Territories.xlsx`
   — recalculates all live formulas via LibreOffice so the workbook opens
   with correct cached values (openpyxl never evaluates formulas itself)
7. `tools/validate_setup.py` — needs (3); checks the scenario against
   every rule in `data/rules.json` (exact budget spend, starting-purchase
   stacking cap, land unit at every foreign border, coastal-only naval
   purchase, every carrier has an escort, no two factions share a sea
   zone, >=6 unit types per faction). Exits non-zero on any violation.
8. `tools/render_map.py` — `data/territories.json` + `data/factions.json`
   + `assets/base_map.png` → `exports/map.png`
9. `tools/extract_territory_shapes.py` — `data/territories.json` +
   `assets/base_map.png` → `data/territory_shapes.json`; vector polygon
   outlines per land territory and sea zone (game-client data, not used
   elsewhere in this repo). Shares land-classification logic with (8) via
   `tools/map_geometry.py`. Fully regenerable — kept in `data/` for the
   same reason as (1).

Not part of `build_all.py`: `tools/render_shapes_preview.py` draws
`exports/territory_shapes_preview.png` from `data/territory_shapes.json` (sea
zones in stable pastels, land in faction colours, outlines dark) for eyeballing
the extracted shapes. Like `exports/map.png` it is a gitignored reference image,
regenerated on request.

`tools/debug_adjacency.py` draws `data/adjacency.json` as single-colour lines
between centre points (`--only sea-sea|land-land|land-sea` filters the kind of
pair; `--out` sets the file). `--diff` instead colours pairs by agreement with
the outlines (green agree, red data-only, magenta outline-only). It also warns
about outlines that are identical or fully hidden.

## Editing territory data via the spreadsheet

`data/territories.json` is canonical, but you don't have to edit it by
hand — you can edit the `All Territories` tab in
`exports/GC1972_Territories.xlsx` (reassign a faction, change a Value,
toggle a Strategic Center, rename a territory) and pull those edits back
in with:

```
python3 tools/sync_territories_from_xlsx.py          # writes data/territories.json
python3 tools/build_all.py                           # regenerates everything downstream
```

Add `--dry-run` to `sync_territories_from_xlsx.py` to preview the diff
without writing anything. It validates before writing — an unknown
Territory ID, an invalid Faction code, an out-of-range Value, or a
missing row all abort with nothing written — and it will not add or
remove territories (every row must match an existing id 1:1). See the
script's docstring for what it deliberately does *not* handle: reassigning
a territory's faction here can make existing entries in
`data/scenarios/starting_setup_200ipc.json` stale (a purchase recorded
under the territory's old faction), which `tools/validate_setup.py` does
not yet catch cleanly. Treat a faction reassignment as the start of a
scenario edit, not a fire-and-forget spreadsheet tweak.

## Adding or changing a scenario

Scenario design (which units go where, promotions, carrier escorts, naval
zone overrides) is still a manual/assisted process, not automated — it
depends on faction doctrine, budget-exact trimming, and cross-faction
sea-zone collision avoidance. `tools/generate_scenario.py` automates this:
`generate_scenario()` is parameterized (budget, whether Strategic Centers
apply, minimum unit-type diversity, promotion count, whether baseline
garrison covers every territory or only foreign-bordering ones) so it can
produce ruleset variants, not just the canonical scenario.

Two scenarios exist today:
- `data/scenarios/starting_setup_200ipc.json` — the canonical scenario:
  200 IPC, Strategic Centers apply (cost discount + cap bonus), >=6 unit
  types, 3 promotions per faction. Built by `tools/generate_scenario.py`.
- `data/scenarios/starting_setup_100ipc.json` — a smaller, faster-setup
  alternative: 100 IPC, no Strategic Centers at all (flat value+2 cap, no
  cost discount), >=5 unit types, 0 promotions. A small (1-2 IPC) leftover
  is acceptable here rather than forced-exact, since the ruleset has far
  fewer denominations to hit an exact total with. Built by
  `tools/generate_scenario_100ipc.py`, a thin wrapper around the same
  `generate_scenario()` function with these parameters.

`tools/build_setup_tab.py`'s `build_setup_tab()` function is similarly
parameterized (scenario path, sheet name, use_sc, min_types) and renders
both: `python3 tools/build_setup_tab.py` builds the 'Initial Setup' tab
from the 200-IPC scenario and the 'Initial Setup (100 IPC)' tab from the
100-IPC one, in one workbook. `tools/validate_setup.py` takes the same
parameters as CLI flags (`--scenario`, `--no-sc`, `--min-types`,
`--budget-tolerance`) — see its docstring for both scenarios' exact
invocations.

A further scenario (e.g. a different budget or map state) can be dropped
in the same way: call `generate_scenario()` with new parameters, add a
`build_setup_tab()` call for it, and validate with the matching flags.

## Verifying a change didn't regress game balance

After editing any `data/` file and re-running the pipeline:

1. `tools/validate_setup.py` must report `No validation errors.`
2. Each faction's row in the Initial Setup tab's Faction Summary should
   show `IPC Unspent = 0` and `Promotions = 3`.
3. No row in any faction's Stacking Cap Check table should read `OVER CAP`.
