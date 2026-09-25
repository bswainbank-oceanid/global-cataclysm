# Data model: modules

The game's reference data lives in **modules**: JSON documents under
`data/modules/<module_dir>/<id>.json`, one document per module instance. The
engine, server, bots and client read them through a small repository layer
(`engine/repository.py`), which a real database can replace later without the
engine noticing. A **Scenario** module ties one instance of each other module
together into a playable game; nothing in the engine names a map, a unit type,
or a faction.

Humans edit module **spreadsheets** (`sheets/<ModuleType>.ods`, one workbook per
module type); `tools/import_sheets.py` writes the edits back into the JSON when
asked, and `tools/export_sheets.py` regenerates the workbooks from the JSON.
The JSON is the only thing the game reads.

Source: `reference/GC Data Model.rtf`, plus the decisions recorded below.

## Conventions

- Every module document carries `module_type`, `id` and `name`; the file name is
  `<id>.json`. Ids of the shipped game are prefixed `GC72_`.
- References between modules are by id (`map_id`, `unit_set_id`, ...).
- Location ids are the map's integers (1-144 on the GC72 map); faction ids are
  the faction codes (`NAA`, `UE`, ...); unit type ids are the unit names
  (`Infantry`, `Mechanized Infantry`, ...).
- List order is meaningful where the engine iterates (locations, unit types,
  factions, adjacency) and is preserved exactly.
- Fields marked *generated* are written by tools, not by hand, and are not
  imported back from the spreadsheets.

## Modules

| Module type | Dir | GC72 instance | Holds |
|---|---|---|---|
| Map | `map` | `GC72_Map` | image, size, flat/cylinder, locations (name, land/sea, anchor point, *generated* boundary polygons, label metrics, adjacency), adjacency overrides |
| AbilityCatalog | `ability_catalog` | `GC72_Abilities` | the engine's fixed ability library: id, name, description template, parameters and their defaults |
| UnitSet | `unit_set` | `GC72_UnitSet` | unit types: stats, purchasable, land/sea combat resolution order, display order, icon, abilities with parameters |
| MapValues | `map_values` | `GC72_MapValues` | map id; each land location's value |
| FactionSet | `faction_set` | `GC72_FactionSet` | factions: id, name, color, icon |
| FactionAssignment | `faction_assignment` | `GC72_FactionAssignment` | value assignment id, faction set id; each land location's faction and starting-setup sea deployment location |
| SCAssignment | `sc_assignment` | `GC72_SCAssignment` | value assignment id, SC bonus (added to a Strategic Center's value), the Strategic Center locations |
| InitialSetup | `initial_setup` | `GC72_StandardSetup`, `GC72_DefensiveSetup` | SC assignment id, unit set id, generation parameters (budget, SC use, diversity); units per location, each with id, unit type and faction |
| UnitPromotions | `unit_promotions` | `GC72_StandardPromotions`, `GC72_DefensivePromotions` | initial setup id; starting promotions per setup unit id |
| Objectives | `objectives` | `GC72_Objectives` | bot objectives: id, name, primary/secondary |
| PrimaryObjectiveOrder | `primary_objective_order` | `GC72_PrimaryOrder` | the order the bot plans its primary objectives in, and each step's share of the planning budget |
| StrategyThresholdSet | `strategy_threshold_set` | `GC72_StrategyThresholds` | strategies, each with per-objective min/max risk and weight; distance weights |
| FactionWeightSet | `faction_weight_set` | `GC72_FactionWeights` | faction set, unit set and threshold set ids; per-faction unit weights and strategy weights |
| RuleSet | `rule_set` | `GC72_Rules` | the rules (what `data/rules.json` held): combat, movement, purchase, production, promotion, victory, game-start defaults |
| Scenario | `scenario` | `GC72_Scenario` | the ids of everything above that make one game, including a standard and a defensive setup |

### Scenario

```json
{
  "module_type": "Scenario", "id": "GC72_Scenario", "name": "Global Cataclysm: 1972",
  "map_id": "GC72_Map", "ability_catalog_id": "GC72_Abilities", "unit_set_id": "GC72_UnitSet",
  "faction_set_id": "GC72_FactionSet", "map_values_id": "GC72_MapValues",
  "faction_assignment_id": "GC72_FactionAssignment", "sc_assignment_id": "GC72_SCAssignment",
  "rule_set_id": "GC72_Rules",
  "setups": {
    "standard":  {"initial_setup_id": "GC72_StandardSetup",  "unit_promotions_id": "GC72_StandardPromotions"},
    "defensive": {"initial_setup_id": "GC72_DefensiveSetup", "unit_promotions_id": "GC72_DefensivePromotions"}
  },
  "bots": {"faction_weight_set_id": "GC72_FactionWeights", "strategy_threshold_set_id": "GC72_StrategyThresholds",
           "objectives_id": "GC72_Objectives", "primary_objective_order_id": "GC72_PrimaryOrder"}
}
```

HUMAN and BOT seats take their starting units from the `standard` setup,
DEFENSIVE seats from the `defensive` one. There is one scenario for now and no
picker on the launch screen; the server and tools use `GC72_Scenario` by
default.

### Abilities

A unit type's special abilities are entries from the fixed ability library the
engine implements. Each entry names an ability id and may set its parameters;
anything left out takes the catalog's default. An ability id the catalog does
not list is a validation error: a new ability needs engine code.

| Ability | Parameters | Effect |
|---|---|---|
| `mustering` | | may be purchased into a contested territory |
| `dig_in` | `defense_bonus` (1) | + defense while defending, above the promotion cap |
| `heroic` | `max_promotions` (5) | may earn more promotions than the rule set's default |
| `amphibious` | `transport_unit` (`Transport`) | may enter sea spaces, becoming that transport unit |
| `blitz` | | a two-step combat move may pass through an empty enemy territory, capturing it |
| `transport` | | the sea form of an amphibious unit: no attack, carries its unit |
| `air_superiority` | `attack_die`, `damage` (null: its normal ones), `triggers_round` | rolls this die/damage in the air superiority round; a `triggers_round` unit on either side makes the round happen |
| `interception` | | enemy aircraft may not fly over a space it holds |
| `carrier_air_wing` | `capacity` (3) | carries air units; they may take off from and land on it (the capacity is shown, not enforced -- as before) |
| `submerge` | | cannot hit or be hit by air units |
| `bombardment` | | may bombard an adjacent land space from the sea as its combat move |
| `indiscriminate` | | picks targets without the same-type preference |

Each ability has a description template shown in the unit's ability list; an
ability with `listed: false` works but is not shown there (as today).

### Unit type fields

`id`, `name`, `category` (Land/Air/Sea), `cost`, `sc_cost`, `attack_die`,
`damage`, `defense`, `hp`, `combat_move`, `non_combat_move`, `purchasable`,
`land_order` / `sea_order` (the position in a land or sea battle's resolution
order; null for a unit that doesn't fight there), `display_order` (the purchase
panel), `icon` (an `assets/icons` file), `abilities` (`[{"id", "params"}]`).

### Strategic Centers

A Strategic Center's value is its location value plus the SC assignment's
`sc_bonus` (2 for GC72). That feeds income and the per-turn deploy cap, as
before. The SC purchase discount is each unit type's own `sc_cost`.

### Starting sea deployment

`FactionAssignment.locations[].sea_deployment_location_id` says which sea zone a
coastal land location's naval purchases go to when a starting setup is
generated, so neighbouring factions never share a zone. It is a setup-generation
input only; in play, naval purchases follow the purchase rules.

### Initial setups

Units are listed where they start (a sea zone for ships and the aircraft on
their carriers). Each carries an `id` (unique in the setup), `unit_type_id`,
`faction_id` and, when it was bought at a different land location,
`purchased_at` (used only by the setup validator's stacking-cap check). An air
unit starting in a sea zone is on its faction's carrier there. Units are placed
in id order within each faction, which is what keeps unit ids and a seeded game
unchanged.

## Client data

`tools/sync_client_data.py` still copies files into `client/data`: it resolves
the scenario and writes the views the client reads (territories, shapes,
factions, units with abilities and icons, adjacency) plus the map image.

## Tools

| Tool | Does |
|---|---|
| `tools/validate_modules.py` | schema and cross-reference checks for every module |
| `tools/export_sheets.py` | module JSON -> `sheets/*.ods` |
| `tools/import_sheets.py` | `sheets/*.ods` -> module JSON (editable fields only; `--check` shows the diff) |
| `tools/extract_territory_shapes.py` | map image -> the Map's boundary polygons |
| `tools/compute_adjacency.py` | boundaries + overrides -> the Map's adjacency |
| `tools/generate_scenario*.py` | writes an InitialSetup + UnitPromotions |
| `tools/validate_setup.py` | checks a setup against the rules |
| `tools/golden_games.py` | seeded full-game digests, to prove a refactor changed nothing |
