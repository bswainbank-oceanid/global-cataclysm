# Regeneration pipeline

Everything under `derived/` and `exports/` is generated from `data/` +
`tools/`. Run the whole thing with:

```
python3 tools/build_all.py
```

which runs, in order:

1. `tools/compute_foreign_neighbors.py` — `data/territories.json` +
   `data/adjacency.json` → `derived/adjacency_foreign.json`
2. `tools/compute_distant.py` — needs (1); recomputes the `distant` flag
   stored on each land territory in `data/territories.json` in place (zero
   same-faction land neighbors — see `data/rules.json`
   `map.distant_flag_definition`). Written back into `data/territories.json`
   itself rather than a `derived/` file because `tools/render_map.py` reads
   it per-space.
3. `tools/compute_faction_profile.py` — needs (1) →
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
   every rule in `data/rules.json` (exact budget spend, stacking cap,
   mandatory infantry at foreign borders, coastal-only naval purchase,
   every carrier has an escort, no two factions share a sea zone). Exits
   non-zero on any violation.
8. `tools/render_map.py` — `data/territories.json` + `data/factions.json`
   + `assets/base_map.png` → `exports/map.png`

## One-time / manual steps (not part of build_all.py)

- `tools/export_adjacency.py <path-to-graph.pkl>` — migrates the original
  Delaunay-triangulation pickle into `data/adjacency.json`. Only needed
  again if the underlying adjacency graph itself changes (e.g. a new
  territory is added and needs to be triangulated in, not just
  distance-fallback approximated).

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
sea-zone collision avoidance. `data/scenarios/starting_setup_200ipc.json`
is the output of that process. A new scenario file (e.g. a different
budget or map state) can be dropped in alongside it; `build_setup_tab.py`
and `validate_setup.py` both take the scenario path as their one real
input and would need a one-line change to point at a different file (this
isn't parameterized via CLI arg yet — see the note in
`tools/build_setup_tab.py` / `tools/validate_setup.py`).

## Verifying a change didn't regress game balance

After editing any `data/` file and re-running the pipeline:

1. `tools/validate_setup.py` must report `No validation errors.`
2. Each faction's row in the Initial Setup tab's Faction Summary should
   show `IPC Unspent = 0` and `Promotions = 3`.
3. No row in any faction's Stacking Cap Check table should read `OVER CAP`.
