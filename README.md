# Global Cataclysm: 1972

Design and setup tooling for an alternate-history wargame (1972 divergence,
six factions: NAA, UE, UER, GPC, PAF, AAC) played on a 3500×1958px map of
149 territories.

This repo is structured so it can also serve as the data backbone for a
computer-game implementation: every rule and every piece of reference data is
plain, versioned JSON under `data/modules/`, not baked into code. A Scenario
module ties a map, unit set, factions, territory allocation, rules, starting
setups and bot settings together, so new ones need no code changes. See
`docs/DATA_MODEL.md` for the modules and `docs/GAME_ARCHITECTURE.md` for the
engine.

## Layout

- `data/modules/` — the game data, one JSON document per module instance.
  See `docs/DATA_MODEL.md` and `docs/SCHEMA.md`.
- `sheets/` — the module spreadsheets people edit (one workbook per module
  type), imported into the JSON by `tools/import_sheets.py`.
- `derived/` — regenerated from the modules. Never edit by hand.
- `tools/` — the scripts that regenerate map data, derived files, sheets and
  exports. See `docs/PIPELINE.md`.
- `exports/` — build products: the rendered map (`map.png`).
- `assets/` — static source assets (the base map image, unit icons).
- `reference/` — the source rulebook and design documents, for provenance.
- `docs/` — data model, schema and pipeline documentation.

## Quick start

```
python tools/build_all.py
```

regenerates the map data, `derived/`, the sheets and `exports/` end to end,
including validation. See `docs/PIPELINE.md` for what each step does.

## Editing game data

Edit the workbook in `sheets/` (territory values and owners, Strategic Centers,
unit stats and abilities, factions, starting setups, bot weights, rules), then

```
python tools/import_sheets.py --check --diff
python tools/import_sheets.py
```

Restart the server afterwards, and run `python tools/sync_client_data.py` for
the client. See `docs/PIPELINE.md`.
