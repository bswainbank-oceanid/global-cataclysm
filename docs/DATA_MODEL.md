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
| FactionSet | `faction_set` | `GC72_FactionSet` | factions: id, name, color, icon; the built-in `neutral` (NEU, the shared Neutral colour) and `noncombatant` (NCB) factions |
| FactionAssignment | `faction_assignment` | `GC72_FactionAssignment` | value assignment id, faction set id; each land location's faction and starting-setup sea deployment location |
| SCAssignment | `sc_assignment` | `GC72_SCAssignment` | value assignment id, SC bonus (added to a Strategic Center's value), the Strategic Center locations |
| InitialSetup | `initial_setup` | `GC72_StandardSetup`, `GC72_NeutralSetup` | SC assignment id, unit set id, generation parameters (budget, SC use, diversity); units per location, each with id, unit type and faction; optional `carryover_mpc` per faction (joins its starting treasury) |
| UnitPromotions | `unit_promotions` | `GC72_StandardPromotions`, `GC72_NeutralPromotions` | initial setup id; starting promotions per setup unit id |
| Objectives | `objectives` | `GC72_Objectives` | bot objectives: id, name, primary/secondary |
| PrimaryObjectiveOrder | `primary_objective_order` | `GC72_PrimaryOrder` | the order the bot plans its primary objectives in, and each step's share of the planning budget |
| StrategyThresholdSet | `strategy_threshold_set` | `GC72_StrategyThresholds` | strategies, each with per-objective min/max risk and weight; distance weights |
| FactionWeightSet | `faction_weight_set` | `GC72_FactionWeights` | faction set, unit set and threshold set ids; per-faction unit weights and strategy weights; `neutral_unit_weights` (a generated scenario's Neutral units) |
| RuleSet | `rule_set` | `GC72_Rules` | the rules (what `data/rules.json` held): combat, movement, purchase, production, promotion, victory, game-start defaults |
| Scenario | `scenario` | `GC72_Scenario` | the ids of everything above that make one game, including a standard and a neutral setup |
| ScenarioGenerator | `scenario_generator` | `GC72_Generator` | the launcher's New Scenario: the base scenario, the scenario-wide defaults, and each player seat's and the Neutral row's default settings |
| ScenarioSetup | `scenario_setup` | (saved from the launcher) | a saved New Scenario setup: name, description, generator id and the launch screen's settings (not the map they deal) |

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
    "neutral": {"initial_setup_id": "GC72_NeutralSetup", "unit_promotions_id": "GC72_NeutralPromotions"}
  },
  "bots": {"faction_weight_set_id": "GC72_FactionWeights", "strategy_threshold_set_id": "GC72_StrategyThresholds",
           "objectives_id": "GC72_Objectives", "primary_objective_order_id": "GC72_PrimaryOrder"}
}
```

HUMAN and BOT seats take their starting units from the `standard` setup,
NEUTRAL seats from the `neutral` one. There is one scenario for now and no
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
| `carrier_air_wing` | `capacity` (3) | carries up to `capacity` of its owner's aircraft; they may take off from and land on it |
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
panel), `icon` (an `assets/icons` file), `plural` (the name for several;
default: name + "s"), `abilities` (`[{"id", "params"}]`).

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

### New Scenario (ScenarioGenerator)

The launcher can start the fixed scenario or a **new** one, dealt afresh at game
start by `engine/scenario_generator.py` from the game's seed and the settings on
the launch screen; the launcher remembers the last ones used. See
`reference/GC72 New Scenario.odt` and the module docstring for the deal.

Settings come at two levels:

- **Scenario-wide** (`GC72_Generator.defaults`): faction weight, SC bonus, the two
  SC minimum distances, extra non-SC units, Infantry in every territory (players,
  and Neutral), and the two surrender thresholds.
- **Per seat** (`seat_defaults`, and `neutral_defaults` for the Neutral row):
  territory value, initial MPC, units MPC, promotions and Strategic Centers (the
  Neutral row has no initial MPC). A player starts with its initial MPC, buys its
  starting units with its units MPC out of it, and keeps the rest; initial MPC is
  at least units MPC. The seats' territory values together may be at most the
  map's total land value (152 on GC72) -- more is scaled down before the deal --
  and the rest of the map is Noncombatant. A player's territory value left at its
  default is the total split evenly among the players; the Neutral one, what they
  leave.

A new scenario's seats are Human, Bot or Not playing: a Not playing seat's faction
is left out of the game altogether. The deal is a set of throwaway modules
(Scenario, FactionAssignment, SCAssignment, InitialSetup/UnitPromotions for the
players and the Neutral pool, and the RuleSet with the chosen surrender thresholds)
held in memory by an `OverlayRepository` on top of the base scenario's; the game
reads them through its own `GameConfig`, and the server sends the client the
game's starting owners, Strategic Centers and SC bonus with every `state` message.

### Saved scenario setups (ScenarioSetup)

The launch screen can save a New Scenario's settings under a name
(`server/setups.py`): everything on the screen -- the seats, the Neutral row, the
alliance and rule choices and the scenario-wide options -- but not the map they
deal, which is new every game. Saved setups are listed in the launcher's scenario
list after the fixed scenario and New scenario; picking one fills the screen with
its settings. The server assigns the id (`Setup_001`, ...). Saving under the name it
was saved with updates that setup; a new name makes a new one (names are unique).
Only settings that could start a game are saved, and never a seed. The fixed
scenario can't be saved.

```json
{"module_type": "ScenarioSetup", "id": "Setup_001", "name": "Duel", "description": "...",
 "generator_id": "GC72_Generator",
 "settings": {"scenario": {"kind": "new", "options": {...}, "neutral": {...}}, "seats": [...],
              "randomize_order": true, "can_withdraw": true, "can_rejoin": false, "max_alliance_size": 3,
              "allow_combat_first_turn": false, "allow_noncombat_first_turn": true}}
```

Land owned by the built-in Neutral faction (NEU) plays like a Neutral seat, but its
Strategic Centers are real ones for whoever captures them; land owned by the
built-in Noncombatant faction (NCB) plays like a Noncombatant seat.

Saved setups are the game's **shared scenarios**: every player sees them, and only an
admin (Scenarios mode) may change, add or delete them. Players' own scenarios are not
modules: they are player data, in the server database (below).

## Player data (server database)

Accounts, players' own scenarios and settings, games and chat are **player data**, not
game content: they live in one SQLite file the server owns,
`server_data/global_cataclysm.sqlite3` (git-ignored; `--db` on the server picks another).
Each record is a JSON document in a `doc` column; the fields the server looks things up
by are copied into columns of their own, with the database enforcing uniqueness. All
access goes through one storage module (`server/store.py`), so the database can be
swapped later without the rest of the server noticing. Ids are server-assigned, with a
prefix per kind (`U_000001`, `S_000001`, `G_000001`).

| Table | Key | Lookup columns | Holds |
|---|---|---|---|
| `users` | `id` | `email` (unique, case-insensitive), `player_name` (unique, case-insensitive) | a player account |
| `logins` | `token_hash` | `user_id` | a client staying logged in |
| `scenarios` | `id` | `owner_id`, `name` (unique per owner) | a player's own scenario |
| `user_settings` | `user_id` + `scenario_id` | | a player's saved settings for a shared scenario |
| `games` | `id` | `code` (unique), `host_id`, `status` | a game, from its lobby to its end |
| `game_saves` | `game_id` | | a live game's saved state |
| `chat` | `id` (in order) | `room` | one chat message |

### User

```json
{"id": "U_000001", "player_name": "Brian", "actual_name": "Brian S.", "email": "b@example.com",
 "password": {"scheme": "pbkdf2_sha256", "iterations": 600000, "salt": "<hex>", "hash": "<hex>"},
 "admin": false, "created": "2026-10-04T15:00:00Z"}
```

- The email and player name are each unique, ignoring case; the email is the login. A player
  name is at most 24 characters; the actual name may be left blank (`server/accounts.py`).
- A password is at least one character. Only its salted hash is stored
  (PBKDF2-SHA256 from Python's standard library), never the password itself.
- `admin` opens Scenarios mode. There's no UI for it: `tools/set_admin.py` sets it in the database.
- No email verification and no password reset for now (a forgotten password is fixed in
  the database). One account may be logged in on several clients at once.

### Login

Logging in or registering returns a random token. The client keeps the token (not the
password) and logs back in with it on every start, until the player logs out, which deletes
it here. Only the token's SHA-256 is stored, so a copy of the database can't log anyone in.

```json
{"token_hash": "<hex>", "user_id": "U_000001", "created": "...", "last_used": "..."}
```

### Scenarios: shared, own, and a player's settings

A game is started from a **scenario** (the doc's "game mode": the same thing). There are three kinds:

- **GC72**, the fixed scenario: its settings are fixed, and not even an admin can change them.
- **Shared scenarios**, the ScenarioSetup modules above: everyone sees them; only an admin
  changes them.
- **A player's own scenarios**, in `scenarios`: only their owner sees them in New Game,
  and only their owner changes or deletes them. Another player sees one only as the
  settings of a game lobby its owner created.

```json
{"id": "S_000001", "owner_id": "U_000001", "name": "Island hopping", "description": "...",
 "generator_id": "GC72_Generator", "settings": {...the launch settings, as a ScenarioSetup's...}}
```

A player can **Save** their own settings for GC72, the blank New Scenario or a shared scenario;
picking that scenario afterwards fills New Game with them, and **Reset Settings** deletes them,
going back to the scenario's own. Nothing is saved without Save. On a player's own scenario, Save
updates the scenario itself and Reset Settings goes back to its last saved version. Saving under a
new name makes a new scenario of the player's own, whichever New Scenario it was started from
(GC72's settings only ever save as personal settings). `server/scenarios.py` holds these rules.

```json
{"user_id": "U_000001", "scenario_id": "Setup_002", "settings": {...}}
```

### Game

```json
{"id": "G_000001", "code": "K7QM2X", "host_id": "U_000001", "status": "forming",
 "scenario": {"kind": "fixed" | "shared" | "own", "id": "Setup_002", "name": "Duel"},
 "settings": {...the launch settings, frozen when the game is created...},
 "seats": [{"seat": 1, "mode": "HUMAN", "faction": "random", "user_id": "U_000001"},
           {"seat": 2, "mode": "HUMAN", "faction": "UE", "user_id": null},
           {"seat": 3, "mode": "BOT", "faction": "random", "user_id": null}, ...],
 "factions": {"1": "UER", "2": "UE", ...},
 "progress": {"round": 4, "turn": 2, "active_faction": "GPC", "waiting_for": ["U_000002"]},
 "created": "...", "started": "...", "ended": "..."}
```

- **status:** `forming` (in its lobby) → `live` → `finished`; a forming game the host
  cancels becomes `cancelled`.
- **Seats** are the settings' seats. Every HUMAN seat is open until a player takes it;
  players pick their own seats, the host too, and one player may take several. A seat's
  faction is known in the lobby when the settings name it, and is dealt at launch when
  they say `random`. `factions` (seat → faction) is filled in at launch.
- The host can launch once every HUMAN seat is taken. A game with exactly one HUMAN seat
  skips the lobby: the host takes the seat and it starts at once. A game with none (all
  bots) starts at once too, owned by its host, who watches it.
- Every forming game is listed under Available Games while it has open seats (private
  games come later). The `code` (six letters and digits, no look-alikes) finds it directly.
- **progress** is copied from the live game after every phase, for My Live Games: the
  round and turn, whose turn it is, and the players it is waiting for.
- A game waits for as long as a human it needs is away (removing players and bot
  replacements come later).

### Game save

A live game's save, so it survives a server restart (`server/persist.py`): a **snapshot** taken at
the start of each faction's turn -- the one moment nothing is in flight (no bot plan half used,
nothing staged, no battle queued) -- plus every **decision** the game has handled since (purchases,
moves, diplomacy, answers, surrenders, "next"). Loading rebuilds the game from the snapshot and
replays the decisions through the same code; bots and dice draw from random generators saved in the
snapshot, so the replay comes out exactly as the game went. A New Scenario's generated modules are
saved with it; everything else is read from the modules as they are when it loads.

```json
{"game_id": "G_000001", "saved": "...",
 "session": {"version": 1,
             "setup": {"seats": [...], "settings": {...}, "generated": [...modules] | null, "scenario_id": ... | null},
             "snapshot": {"game_state": {...}, "combat_rng": [...], "engine": {...}, "turn_log": [...],
                          "stats": {...}, "bots": {"UER": {"kind": "strategy", "rng": [...], "base_style": ..., "budget": ...}},
                          "strategy_logs": {...}, "session": {...the pending invitation and armistice...}},
             "decisions": [{"type": "stage_purchase", ...}, {"type": "next"}, ...]}}
```

A Claude bot asks the API again on a replay, so a game with one may not replay exactly.

### Chat

```json
{"id": 1, "room": "browse" | "G_000001", "user_id": "U_000001", "player_name": "Brian",
 "text": "anyone up for a duel?", "sent": "..."}
```

`browse` is the Available Games chat, for everyone browsing it; a game's id is its lobby's
chat. In-game chat comes later. A player name is copied into each message so old messages
read the same.

## Client data

`tools/sync_client_data.py` still copies files into `client/data`: it resolves
the scenario and writes the views the client reads (territories, shapes,
factions, units with abilities and icons, adjacency) plus the map image.

## Tools

| Tool | Does |
|---|---|
| `tools/validate_modules.py` | schema and cross-reference checks for every module |
| `tools/export_sheets.py` | module JSON -> `sheets/*.ods` |
| `tools/import_sheets.py` | `sheets/*.ods` -> module JSON (`--check --diff` shows what would change; writes only if everything still validates) |
| `tools/extract_territory_shapes.py` | map image -> the Map's boundary polygons |
| `tools/compute_adjacency.py` | boundaries + overrides -> the Map's adjacency |
| `tools/generate_setup.py` | writes an InitialSetup + UnitPromotions from the setup's `generation` parameters |
| `tools/validate_setup.py` | checks the starting setups against the setup rules |
| `tools/sync_client_data.py` | the scenario's modules -> `client/data` |
| `tools/golden_games.py` | seeded full-game digests, to prove a refactor changed nothing |
| `tools/set_admin.py` | turns a player's admin flag on or off in the player database (`--list`: who is an admin) |
| `tools/launcher.py` | starts the game as a player does: syncs the client data, starts the server in the background if it isn't running, opens the game window (what the shortcut runs) |
| `tools/make_shortcut.py` | makes the "Global Cataclysm 1972" desktop and Start menu shortcuts, and "(Player 2)" ones with their own saved login (`--profile player2`), with their icons in `assets/logo` |

Tools find the modules through `tools/tool_data.py` and take `--scenario`
(default `GC72_Scenario`). See `docs/PIPELINE.md` for the editing workflow.
