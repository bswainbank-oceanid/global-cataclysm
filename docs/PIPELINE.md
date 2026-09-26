# Data pipeline

The game reads only the module JSON under `data/modules/` (`docs/DATA_MODEL.md`).
People edit the module spreadsheets under `sheets/`; tools regenerate the parts
that come from the map image. Everything under `derived/` and `exports/` is
regenerable.

## Editing game data

1. Open the workbook for the module type in `sheets/` (`UnitSet.ods`,
   `MapValues.ods`, `FactionAssignment.ods`, `FactionWeightSet.ods`, ...). Each
   has a **Modules** sheet (one row per module instance, with its single-valued
   fields — nested ones as dotted names like `generation.budget`) and a sheet
   per list (one row per item, first column the module id). Row order is list
   order. Columns marked `(info)` are for reading only.
2. Save it (as .ods).
3. Preview what the import would change, then import:

   ```
   python tools/import_sheets.py --check --diff
   python tools/import_sheets.py
   ```

   Nothing is written unless every module still validates. A module that no
   workbook mentions is left alone; fields the sheets don't carry (boundaries,
   adjacency, label metrics) are kept.
4. Restart the server and re-sync the client (`python tools/sync_client_data.py`)
   — the engine caches module data per process.

To add a module instance (a second unit set, a new territory allocation), add a
row to the Modules sheet with a new id and its rows to the list sheets, then
point a Scenario at it.

After a tool changes the JSON (the map tools, a setup generator), refresh the
workbooks so they show what the game reads:

```
python tools/export_sheets.py
```

## Regenerating from the map image

```
python tools/build_all.py
```

runs, in order (stopping at the first failure):

1. `tools/extract_territory_shapes.py` — the map image + each location's anchor
   point → the Map's `boundary` polygons.
2. `tools/compute_adjacency.py` — boundaries + `adjacency_overrides` → the Map's
   `adjacency` (prints any override that no longer has an effect).
3. `tools/compute_foreign_neighbors.py` → `derived/adjacency_foreign.json`.
4. `tools/compute_faction_profile.py` → `derived/faction_territory_profile.json`.
5. `tools/validate_modules.py` — fields, unique ids and cross-references of
   every module.
6. `tools/validate_setup.py --setup standard` — the standard starting setup
   against the setup rules.
7. `tools/export_sheets.py` — the workbooks.
8. `tools/render_map.py` → `exports/map.png`.

All tools take `--scenario` (default `GC72_Scenario`).

Not part of `build_all.py`: `tools/render_shapes_preview.py` draws
`exports/territory_shapes_preview.png` (sea zones in pastels, land in faction
colours) for eyeballing the extracted shapes, and `tools/debug_adjacency.py`
draws the adjacency as lines between anchor points (`--only
sea-sea|land-land|land-sea`, `--diff` to colour by agreement with the outlines).

## Starting setups

A scenario has a standard setup (HUMAN/BOT seats) and a defensive one
(DEFENSIVE seats), each an InitialSetup + UnitPromotions module.

```
python tools/generate_setup.py --setup standard --check   # would regenerating change it?
python tools/generate_setup.py --setup standard           # regenerate it
python tools/validate_setup.py                            # both setups against the rules
```

`generate_setup.py` works from the setup's own `generation` parameters (budget,
use_sc, cap_bonus, min_unit_types, promotions, budget_tolerance): a garrison
unit in every territory with room, then units drawn by each faction's unit
weights (FactionWeightSet), naval units at up to three coastal territories per
faction using their sea deployment zones, a carrier always bought with an
escorting aircraft, the leftover closed with the garrison unit, and the
highest-weighted unit types promoted. It is seeded, so it's reproducible.
Setups can also be edited by hand in `sheets/InitialSetup.ods`.

Known as of the data-model refactor: the stored standard setup predates later
unit-weight changes (the generator would now produce a different one), and the
stored defensive setup fails validation on current unit costs (three factions
spend 101 of 100 MPC; Eastern United States holds 5 of 4). Both are left as
they were so the game plays exactly as before.

## Verifying a refactor changed nothing

```
python tools/golden_games.py --check tools/golden_games.json
```

replays five seeded bot games (random and strategy bots, Defensive/Neutral
seats, alliances) and compares digests of the starting board, the full turn log
and the final board with the recorded baseline.
