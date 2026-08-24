# Regeneration pipeline

Everything under `derived/` and `exports/` is generated from `data/` +
`tools/`. Run the whole thing with:

```
python3 tools/build_all.py
```

which runs, in order:

1. `tools/compute_foreign_neighbors.py` — `data/territories.json` +
   `data/adjacency.json` → `derived/adjacency_foreign.json`
2. `tools/compute_faction_profile.py` — needs (1) →
   `derived/faction_territory_profile.json`
3. `tools/build_master_xlsx.py` — `data/territories.json` +
   `data/factions.json` → `exports/GC1972_Territories.xlsx` ('All
   Territories' tab + 6 faction subtabs + Unassigned)
4. `tools/build_setup_tab.py` — needs (2) and (3); adds/replaces the
   'Initial Setup' tab on the same workbook from
   `data/scenarios/starting_setup_200ipc.json` + `data/units.json`
5. `/mnt/skills/public/xlsx/scripts/recalc.py exports/GC1972_Territories.xlsx`
   — recalculates all live formulas via LibreOffice so the workbook opens
   with correct cached values (openpyxl never evaluates formulas itself)
6. `tools/validate_setup.py` — needs (2); checks the scenario against
   every rule in `data/rules.json` (exact budget spend, stacking cap,
   mandatory infantry at foreign borders, coastal-only naval purchase,
   every carrier has an escort, no two factions share a sea zone). Exits
   non-zero on any violation.
7. `tools/render_map.py` — `data/territories.json` + `data/factions.json`
   + `assets/base_map.png` → `exports/map.png`

## One-time / manual steps (not part of build_all.py)

- `tools/export_adjacency.py <path-to-graph.pkl>` — migrates the original
  Delaunay-triangulation pickle into `data/adjacency.json`. Only needed
  again if the underlying adjacency graph itself changes (e.g. a new
  territory is added and needs to be triangulated in, not just
  distance-fallback approximated).

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
