# Global Cataclysm: 1972

Design and setup tooling for an alternate-history wargame (1972 divergence,
six factions: NAA, UE, UER, GPC, PAF, AAC) played on a 3500×2000px map of
146 territories.

This repo is structured so it can also serve as the data backbone for a
future computer-game implementation: every rule and every piece of game
state is plain, versioned JSON under `data/`, not baked into ad hoc
scripts.

## Layout

- `data/` — canonical, hand-maintained (or hand-designed) game data.
  Territories, adjacency, unit stats, faction reference, rule constants,
  and the starting-setup scenario. See `docs/SCHEMA.md`.
- `derived/` — regenerated from `data/`. Never edit by hand.
- `tools/` — the scripts that build `derived/` and `exports/` from
  `data/`. See `docs/PIPELINE.md` for run order.
- `exports/` — build products: the editing/reference workbook
  (`GC1972_Territories.xlsx`) and the rendered map (`map.png`).
- `assets/` — static source assets (the base map image).
- `reference/` — the source rulebook and design-doc PDFs, for provenance.
- `docs/` — schema and pipeline documentation.

## Quick start

```
python3 tools/build_all.py
```

regenerates `derived/` and `exports/` from `data/` end to end, including
validation. See `docs/PIPELINE.md` for what each step does and how to
verify a change didn't regress game balance.

## Editing game data

- Territory ownership, values, and Strategic Centers: `data/territories.json`
  (or, for a quick pass, `exports/GC1972_Territories.xlsx`'s 'All
  Territories' tab, then re-export back into JSON — not yet automated,
  see `docs/PIPELINE.md`).
- Unit stats and costs: `data/units.json`.
- Rule constants (stacking cap formula, promotion effect, combat rules,
  etc): `data/rules.json`.
- The starting-setup scenario (what each faction buys, promotes, and
  where naval units deploy): `data/scenarios/starting_setup_200ipc.json`.

Always re-run `python3 tools/build_all.py` after editing and check that
`tools/validate_setup.py` reports no errors before trusting the result.
