# Game implementation plan

This repo currently produces the *design data* for Global Cataclysm: 1972
(territories, adjacency, unit stats, rules, starting scenarios) plus
tooling to validate and preview it. This doc records the plan for turning
that into an actual playable game, and the reasoning behind the choices
made so far. It's a living plan, not a spec — update it as decisions
change.

## Targets

1. **Platforms**, in priority order: desktop, then tablet, then maybe web.
2. **Multiplayer**: eventually up to 6 players in persistent online games
   (join, play a turn, leave, come back later). Initial target is
   **single player vs. bots** — multiplayer comes after that works.
3. **Map feel**: smooth continuous pan/zoom is a tech priority, not a
   nice-to-have.
4. **The map is a cylinder**: it wraps east-west (going off the east edge
   brings you back around the west side); north-south does not wrap. This
   is foundational, not a later feature — see below.

## Engine: Godot

Chosen over Unity and a web-native stack (TypeScript + PixiJS + Electron/
Capacitor) because:
- It's a genuinely 2D-native engine (not a 3D engine adapted for 2D), and
  `Camera2D` gives smooth pan/zoom as a built-in property, not something
  to hand-roll.
- One project exports to Windows/Mac/Linux/iOS/Android/HTML5, matching
  "desktop → tablet → maybe web" as an export-priority order rather than
  separate codebases.
- Free (MIT license), no revenue-based tiers.

**Known limitation to design around**: Godot's C# support does not run in
HTML5/web exports (GDScript does). Resolution: the Godot client itself
(rendering, input, UI) stays in **GDScript**, which is fully
cross-platform including web. The rules/combat engine is a separate
service the client talks to — see "Rules engine" below — so C# (if used
there) never has to live inside the exported client binary, and the web
export path stays open without compromising the rest of the plan.

**Multiplayer networking**: Godot ships a built-in high-level multiplayer
API, but it's shaped for concurrent-session play (host a match), not
"persistent, reconnect anytime." Plan is to skip it and have the client
talk plain HTTP/WebSocket to a custom backend, the same way any web/mobile
app would.

## Rules engine: one module, two deployment modes

The combat/production/promotion rules (already specified in
`data/rules.json` and `data/units.json`) should be implemented as a single
standalone, testable module — not duplicated per-platform:
- **Single player vs. bots**: runs locally (embedded/local process),
  talking to nothing.
- **Multiplayer**: the same module runs server-side as the authority;
  clients submit orders and render whatever state comes back.

This is a turn-based game, not real-time, so the backend doesn't need
low-latency netcode — an authoritative server that validates submitted
orders, resolves a turn, persists new state to a database, and notifies
clients (WebSocket push is enough) is the right shape for "persistent
game" durability.

Note for a **web build** specifically: a browser page can't spawn a local
server process, so even "single player vs. bots" on web would need to talk
to a hosted backend. Fine to defer given web is the lowest/optional
platform priority, but worth remembering if web moves up in priority.

## The cylindrical map: two distinct layers of work

**Layer 1 — rendering/camera (not yet built).** `Camera2D` doesn't wrap on
its own. Standard approach: keep three horizontal copies of the territory
node tree live at once (`x=0`, `x=+3500`, `x=-3500`), all routing
input/clicks back to the same underlying territory data, and wrap the
camera's X position as it crosses a map-width boundary. Cap max zoom-out
below one map-width, or accept a fully-zoomed-out view can show the same
region twice. Moderate, well-understood engineering — not a technical
risk, just work that hasn't been done yet.

**Layer 2 — adjacency data (done as of this session).** The original
adjacency graph was built with plain Euclidean distance, so it had no
concept of wraparound — territories that should be "next to each other
across the seam" (confirmed concretely: Cuba↔Mexico, ~3082px apart in raw
coordinates but ~418px apart measured around the wrap) were missing edges
entirely. Fixed by `tools/compute_adjacency.py`: a Delaunay triangulation
computed on a cylinder (ghost-point technique — triangulate every point
alongside copies of the whole set shifted by ±the map width, keep edges
that touch a real point), same 420px threshold as before. This also
absorbed a second, unrelated problem: the old graph was a frozen
historical artifact that had quietly drifted out of sync with
`territories.json` as positions changed over the course of this project;
it's now a normal regenerable pipeline step instead.

Confirmed the wraparound requirement is real, not cosmetic: Eastern/
Southern/Midwestern US sit at x≈52–152 and Western US/Rocky Mountain
States/Hawaii sit at x≈2929–3443 — the map's seam already **cuts through
the continental US** in the current layout. "Scroll and see the US
centered" only works once Layer 1 exists; it isn't achievable by camera
positioning alone on the raw data.

## Data layer: how `data/` maps onto the game

| File | Runtime role |
|---|---|
| `territories.json` | Drives territory node instancing at load time — position, ownership, value, SC flag. |
| `territory_shapes.json` | Per-territory polygon(s) for `Polygon2D`/`CollisionPolygon2D` — see below. |
| `adjacency.json` | Not visual. Loaded as a plain graph inside the rules engine for movement/adjacency validation. No nodes represent edges on screen. |
| `factions.json` | Lookup table (color, name, doctrine) for territory fill-coloring and UI. |
| `units.json`, `rules.json` | Core config the rules engine reads at startup — not represented as scene nodes. |
| `scenarios/*.json` | Directly becomes "New Game" initial state — already shaped as exactly the per-territory purchase/promotion/escort data a game-start routine needs. |

**`data/territory_shapes.json` — done, land and sea.** `territories.json`
only has a center point and bounding box per territory; the actual
outline existed only implicitly in `assets/base_map.png`'s art.
Classification logic (shared with `tools/render_map.py`'s land color
fill, so the preview map and the game's real shapes can't silently drift
apart) lives in `tools/map_geometry.py`; `tools/extract_territory_shapes.py`
runs `cv2.findContours` on each territory's region(s) and simplifies with
`cv2.approxPolyDP` (2.5px tolerance). Output maps all 149 territories to
a **list** of polygons, not one flat polygon: 6 land island-chain
territories (Cuba, Falkland Islands, Philippines, New Guinea, Hawaii,
Polynesia) are multiple disconnected landmasses, and a couple of sea
zones (Labrador Sea/Gulf of Mexico, Eastern/South-Eastern Indian Ocean)
share a connected water region with no drawn border line in the source
art and get split by nearest-seed-point instead. A sea zone's polygon is
its outer boundary only — it is NOT punched through where an island it
fully encloses sits (e.g. Eastern Indian Ocean/114 around Indonesia,
North Pacific/85 around Hawaii); consumers MUST draw/instantiate land
territories after (on top of) sea territories so enclosed islands show
through correctly. Extraction runs on the flat, non-wrapped image — the
seam literally cuts through the continental US
(confirmed visually) — so stitching polygons across the wrap at render
time is entirely a Layer 1 job, not something baked into this data.

**Authoring workflow stays as-is.** `data/` + the Excel round-trip +
the Python pipeline remain the design-time source of truth, unchanged.
Godot loads the JSON read-only; nobody hand-edits territory data inside
the Godot editor. The game becomes one more thing that regenerates from
the same JSON, not a parallel editing path.

## Build order

1. Rules/combat engine as a standalone, tested module consuming the
   existing JSON data — no rendering yet.
2. ~~Territory shape extraction~~ — done.
3. Single-player desktop client: a Python WebSocket server (`server/`)
   wrapping the engine, and a Godot client talking to it over that API —
   decided this session, even for local single-player, rather than
   embedding the engine in Godot (see `server/`'s own docstrings for why:
   no reimplementing the rules engine in GDScript, no drift risk between
   two copies of the ruleset). Map pan/zoom (including east-west
   wraparound from day one, not bolted on later), territory selection,
   unit deployment UI, and simple bot AI (already built, `engine/bots/`)
   all live on top of that. Server-side, the engine's phase-orchestration
   already existed in `engine/bots/driver.py`'s shape; `server/session.py`
   reuses that same single-while-loop pattern for whichever phases don't
   need a human decision.
4. Tablet input/UI polish.
5. Extend the same `server/` (not a separate backend) with persistence
   (`GameState.to_dict()`/`from_dict()` already exist for exactly this),
   a lobby, multiple simultaneous games, and real auth/session
   management, for remote multiplayer.
6. Web export, if still wanted by then.

## Status

- ✅ Adjacency graph is wrap-aware and fully regenerable
  (`tools/compute_adjacency.py`).
- ✅ Territory shape/polygon extraction (`tools/extract_territory_shapes.py`
  → `data/territory_shapes.json`).
- ✅ Rules engine (standalone module, `engine/`, 381 tests) — Purchase
  (including the carrierless-air and contested-purchase-lost deploy
  fallbacks), Deploy + Income, Combat Move, Combat Resolution, Non-Combat
  Move, Capture Territory, faction elimination, game-end detection, and
  full turn/phase orchestration are implemented and tested, along with a
  random bot (`engine/bots/`) that drives full games through the same
  public API a human UI would use, and a `GameStats` observer
  (`engine/stats.py`) for per-turn/per-game reporting. The full Alliance
  System (invite/accept/withdraw, the Strategic-Center withdrawal lock,
  the rejoin ban, an elimination-aware max-alliance-size cap, and bot
  decision policy for all of it), all three of `combat.
  first_round_bonuses`' cases (amphibious landing, sea-deploy ambush,
  former-ally reclaim — recurring, not one-shot, and exempted from true
  territory loss while a betrayal reclaim is in progress), and `combat.
  true_territory_loss` (a faction that fails to defend its own contested
  land outright now actually loses it — including mid-turn, if that drops
  it below the Strategic Center threshold) are all in too. A faction's
  *other*, currently-uncontested territories staying owned by it after
  elimination, until someone physically attacks them, is confirmed
  intentional — not a gap.
  Known non-blocking future work, not part of "the engine" itself:
  `RandomBot` has no learning/lookahead (deliberate, for now); there's no
  human-facing decision UI for alliance actions (the engine API is
  complete — invite_to_alliance/withdraw_from_alliance take a decision as
  input, same as every other order; a UI just needs to call them).
- ⬜ WebSocket server (`server/`, 13 tests) — first vertical slice only,
  proving the client-server architecture end to end: one hardcoded game
  (NAA the only HUMAN faction, everyone else NEUTRAL), `server/session.py`
  handles the Purchase phase as a real client decision (join/
  submit_purchases/confirm_purchases messages) and auto-drives every other
  phase through to the next decision point or game-over, the same way
  `engine/bots/driver.py` auto-drives a bot's non-decision phases.
  `server/app.py` is the actual asyncio/`websockets` I/O, deliberately
  thin — every real decision lives in `GameSession`, unit-tested directly
  with no socket ever opened; `server/test_client.py` is a small scripted
  client for manual end-to-end verification against a running server
  (`python -m server.app`, then `python -m server.test_client`). Not yet:
  Combat Move/Non-Combat Move/Alliances as real decision points (submitted
  empty for now), multiple simultaneous games, persistence, or real
  auth/session management (a "join" message is trusted at face value).
  This is the project's first external dependency (`websockets`,
  `requirements.txt`) — `engine/` and `tools/` remain stdlib-only.
- ⬜ Godot client / map rendering / wraparound camera.
