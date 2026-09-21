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

Promotion is repeatable (`UnitInstance.promotions`, no fixed cap): every 5 XP after a round is a promotion -- die up one size (max D12), +1 defense (max 10), +1 HP -- and surplus XP carries over; see `data/rules.json` promotion. The combat/production/promotion rules (already specified in
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
entirely. Fixed by `tools/compute_adjacency.py`, first with a Delaunay
triangulation on a cylinder, and now (superseding that, because
centre-point triangulation mis-stated ~245 pairs) by testing which real
outlines in `territory_shapes.json` touch, wrapping east-west. This also
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
- ✅ Rules engine (standalone module, `engine/`, 459 tests) — Purchase
  (including the carrierless-air and contested-purchase-lost deploy
  fallbacks), Deploy + Income, Combat Move, Combat Resolution, Non-Combat
  Move, Capture Territory, faction elimination, game-end detection, and
  full turn/phase orchestration are implemented and tested, along with a
  random bot (`engine/bots/`) that drives full games through the same
  public API a human UI would use, and a `GameStats` observer
  (`engine/stats.py`) for per-turn/per-game reporting. The full Alliance
  System (invite/accept/withdraw, the Strategic-Center withdrawal lock,
  the rejoin ban, an elimination-aware max-alliance-size cap, and bot
  decision policy for all of it -- the heuristic strategy bots (styles, objectives, risk checks; `docs/BOT_STRATEGY.md`) sit beside the random baseline, chosen per seat; an inviting bot rotates through the legal
  targets, asking whoever it asked least recently, so a decliner waits for everyone
  else, and it gives up on a faction after 5 declines: `alliance_policy._pick_target`), all three of `combat.
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
- ⬜ WebSocket server (`server/`, 112 tests) — first vertical slice,
  proving the client-server architecture end to end: one hardcoded game
  (NAA and GPC both BOTs watched by a spectator client -- see the watch
  mode below; the human-play protocol described next is still in place and
  tested, just no longer what the demo server runs). Every one
  of the 7 `turn_order` phases is now a real client decision (Combat
  Resolution excepted -- no player choice in HOW it resolves, only whether
  to attack at all via Combat Move), same one-shot shape for each: a
  single `purchase`/`combat_move`/`noncombat_move`/`alliance_action`
  message carrying the COMPLETE, final decision (decided this session for
  Purchase: the client owns territory selection and MPC budget tracking
  itself, so a separate stage-then-confirm round trip has nothing left to
  teach it -- `your_turn` still includes `legal_purchase_targets`,
  territory-level only, so the client knows where it can buy before
  composing an order; unit types/costs are the client's own `units.json`
  copy's job, same as rendering needs anyway. For Combat Move, decided
  this session: "the legal combat move options for each unit is known at
  turn start" -- `GameEngine.legal_combat_move_options` computes, per unit
  that hasn't moved yet, every legal destination and the path to it, sent
  in the same `your_turn` message as `legal_combat_moves`. For Non-Combat
  Move, decided this session: "non-combat move options can change after
  combat[, so it needs] a similar pattern[: get] the full list of legal
  options after combat[; the] user submits their choices" -- unlike
  Purchase/Combat Move, `GameEngine.legal_noncombat_move_options` is
  deliberately NOT a turn-start snapshot; it's computed fresh only once
  Non-Combat Move is actually reached (after Combat Resolution, and after
  `process_return_to_base`'s automatic air-unit snap-back has already
  run), since what's legal genuinely depends on how combat just played
  out. Its `destinations` are plain territory ids, not `{destination:
  path}` like Combat Move's, since `NonCombatMoveOrder` only ever needs
  the endpoint. This phase is also deliberately permissive about
  carrier/aircraft stranding -- "the client can handle warning the player
  about stranding aircraft[; the] server can allow that as a legal move":
  an order leaving an aircraft over open water with no own carrier is
  legal to submit, and the engine only enforces the actual loss at phase
  end (`movement.stranded_aircraft_rule`), same as it always has -- no
  extra server-side restriction was added. For Alliances, decided this
  session: "give the player the full set of legal options[; they] submit
  their instructions, which might be do nothing (an option for any
  phase)" -- `GameEngine.legal_alliance_options` reports
  `eligible_invite_targets` and `can_withdraw` (a per-faction decision, not
  per-unit, so no per-unit map); an `alliance_action` message's `action`
  is `"none"` (the do-nothing option Purchase/Combat Move/Non-Combat Move
  express as an empty order list instead, since Alliances has no list to
  leave empty), `"invite"` (with a `"target"`), or `"withdraw"`. An
  invite's accept/decline resolves synchronously server-side via
  `engine.bots.alliance_policy.accepts_invite`, exactly like a bot
  inviting another bot -- but that function only ever reads the TARGET's
  own `alliance_strategy`, which `engine.setup.build_game_state` only ever
  sets for BOT-mode factions, so a HUMAN invite target currently always
  declines by default; genuine human-to-human alliance negotiation isn't
  built (not a problem yet since the one demo scenario only ever has one
  human faction in play) -- see `server/session.py`'s own docstring). The
  client picks from whichever `legal_*` options each phase offers and
  sends the complete decision in one message each time, no server-side
  per-unit round trip. `your_turn` is one phase-aware message type
  throughout -- the client tells the phases apart by `phase` and reads
  whichever `legal_*` key is present. Every other phase auto-drives
  through to the next decision point or game-over, the same single-while-
  loop shape `engine/bots/driver.py` uses for a bot's non-decision phases
  -- `server/session.py`'s `_drain_phases` mirrors it directly, stopping
  early (only for a human) right at Combat Move, Non-Combat Move, or
  Alliances to await that decision, and resuming from there once it
  arrives (Alliances, being the last phase, finishes the turn directly
  instead of resuming the drain -- there's nothing left to advance to); a
  human's own `combat_events` (if Combat Move drove any battles) are
  reported the moment they happen even when the drain stops again right
  after at Non-Combat Move, not held back until the whole turn finishes.
  An "invite" against a HUMAN target is the ONE genuinely out-of-turn
  choice in the whole protocol (this session: "add the API for humans to
  accept or reject alliance invitations[; this is] the only out-of-turn
  choice in the game") -- `GameSession` tracks it as `_pending_invite`
  (`{inviter, target}` or `None`); the inviter's turn stays open,
  unfinished, and a `"to"`-routed `alliance_invite` goes to the TARGET
  specifically (not GameState.active_faction -- the one message in this
  protocol sent on behalf of a faction that isn't currently "up"), who
  answers with `alliance_invite_response`; that resolves the exact same
  `GameEngine.invite_to_alliance` call the synchronous BOT-target path
  already used (a BOT target's accept/decline is still resolved
  immediately via `engine.bots.alliance_policy.accepts_invite`, no
  different from a bot inviting another bot), then finishes the
  INVITER's turn, not the responder's. A reconnecting client on either
  side of a pending invite gets that invite's state resurfaced (an
  "invite still pending" ack for the inviter, the same `alliance_invite`
  prompt for the target) instead of a stale/misleading fresh prompt.
  Bot turns and combat resolution are narrated, not just applied silently:
  `engine.turn_log.TurnLog` (a new engine-level observer, alongside
  `stats.GameStats` but an ORDERED per-event log rather than a whole-game
  aggregate — every purchase/move order, every roll-by-roll combat event
  already yielded by `combat.resolve_battle`, every capture/deploy/
  income/alliance action) captures everything as it happens; whenever a
  BOT faction's turn comes up next, the server plays the bot's entire
  turn out immediately (a bot never waits for input) and ships the whole
  turn's events as one `bot_turn` message, and a HUMAN's own Combat
  Resolution (no player choice in HOW it resolves, only in whether to
  attack) sends just its battle events as `combat_events` — both using
  the identical event shape, so one client-side playback UI handles
  either. Pacing is entirely client-side (decided this session): the
  server ships the complete event list in one message and never paces
  delivery itself. `server/app.py` is the actual asyncio/`websockets`
  I/O, deliberately thin — every real decision lives in `GameSession`,
  unit-tested directly with no socket ever opened; `server/test_client.py`
  is a small scripted client (with its own toy playback-pacing loop) for
  manual end-to-end verification against a running server
  (`python -m server.app`, then `python -m server.test_client`, which now
  drives two full NAA turns exercising real `purchase`, `combat_move`,
  `noncombat_move`, and `alliance_action` decisions in sequence -- always
  `"none"` for the alliance action there, since the demo scenario's only
  2 active factions, NAA and AAC, could never actually ally anyway; the
  out-of-turn invite/accept exchange itself was verified live separately,
  with two real client sockets against an ad-hoc two-human session, not
  through the committed single-human demo). Not yet: a HUMAN invite
  target that never answers at all (no timeout/auto-decline -- the
  inviter's turn just stays open indefinitely; not exercised by the demo
  scenario, which has only one human faction); multiple simultaneous
  games, persistence, or real auth/session management (a "join" message
  is trusted at face value -- nothing stops two connections both claiming
  the same faction, which matters a lot more now that a genuinely
  out-of-turn message exists).
  This is the project's first external dependency (`websockets`,
  `requirements.txt`) — `engine/` and `tools/` remain stdlib-only.
- 🔶 Godot client (`client/`, Godot 4.7 GL Compatibility, GDScript) -- the
  map/HUD milestone is built: pannable/zoomable map with seamless
  east-west wraparound (three side-by-side copies, camera folds x back
  into range; smooth cursor-anchored zoom, drag inertia, keyboard and
  trackpad); faction ownership overlay drawn from `territory_shapes.json`
  polygons (unfillable ones cleaned by boolean union at load); zoom-aware
  labels and hover/selection with picking (land before sea -- a sea
  zone's polygon is its outer boundary, so sea highlight fills sit
  beneath the ownership fill); adaptive unit badges (quiet Flag badges at
  world zoom, per-type Strip badges from 1.0x, chosen after comparing
  three styles on real data; zoomed-in badges are drawn at 2x, and a value marker -- the territory value in an owner-coloured disc, inside a gold star for Strategic Centers -- leads each land space's badge row at every zoom level, except spaces worth 0; the number includes the SC bonus); neutral powers' land is filled cream instead of a faction colour (and shows no value marker); a Defensive power has no Strategic Centers: its home territories are switched off at setup (`TerritoryState.sc_disabled`, asked through `GameState.is_strategic_center`) and stay non-SC -- no star, no +2, no SC count -- whoever captures them (the client reads the flag in `GameStore.is_sc`) and is never eliminated by the engine (its units stay until individually killed); significant events -- a faction eliminated, an alliance formed or left, the player's own invitation turned down, and the game's end -- are announced in a panel in the middle of the screen (`AnnouncementWindow`, fed by `TurnStepper._announce` from the executed phase's events and from `game_over`): they queue, are acknowledged one by one with OK (or Enter/Esc), and the game does not run on by itself while one is up; in the player's Combat and Non-Combat Move phases the badge groups of spaces where at least one of their units can still make a legal move throb with a bright gold glow and an outward-rippling ring, at every zoom (`MapUnits._PulseLayer`, driven by the server's move options, so a space stops pulsing once all its movable units are ordered); purchases awaiting deployment -- queued or already confirmed -- show as a second, darker, white-bordered box under a space's units; contested spaces -- and the destinations of a queued combat move -- get diagonal black stripes (a map-space shader); queued moves (combat, non-combat, and the automatic air return-to-base, which `TurnLog` records as `return_to_base` and the stepper queues as its own "Return to Base" step before the rest of Non-Combat Move) are drawn as curved faction-coloured arrows, wider with unit count, bending north/east for origins to the north/east and south/west otherwise, and shortening tail-first over 1s when executed; territory names in the queue/log are links that select and zoom to that space; land units at sea are in transport form -- shown as Transports on the map, and in the selection panel as a single selectable tile per unit (a regular-size Transport icon with no XP/HP of its own, and the carried unit's full package -- star, XP pips, HP boxes -- beside it in a bordered box; about twice a normal tile's width); the panel otherwise lists every unit as a tile (promotion stars above -- one per promotion up to four, then a bigger star with the number --, XP pips below, HP boxes at the right -- white left, red lost; two columns above six HP, and "left/max" written under the unit from five HP up), and promoted units get a gold star (numbered from the second promotion) and, per number of promotions, their own stack on the map (`GameStore.stacks` keys like `Armor|P2`); a HUD following `reference/GC Mockup.pdf`
  (title/round/phase block, one T/MCP/SC/UV/allies panel per seated
  faction -- MCP is the value of the uncontested land it holds, SC bonus included (`GameStore.territory_income`), not its cash -- with eliminated ones dimmed, a selection panel that shows every unit
  individually as a selectable tile (promotion star above, XP pips below, HP boxes at the right -- white for HP left, red for HP lost), an event log fed by `bot_turn`/`combat_events`); and a
  WebSocket `Net` autoload that joins the server and feeds `state` into
  `GameStore`, the single seam every view reads from. NOT built yet: the
  per-phase human decision UIs (purchase, combat/non-combat move,
  alliances, invite response), bot-turn/combat playback, and touch/tablet
  polish -- until those exist the client is a **watcher**: NAA and AAC are
  both bots, and the game is stepped with a **Next button** (or Space).
  The UI pattern is meant to carry over to player turns: the upper right
  panel shows details of the selected space (later: where orders get
  picked), and the lower box holds the **queued orders** for the current
  phase, above a log of what has been executed. The server side is
  `server/stepper.py` (`PhaseStepper`, protocol in its docstring): a
  spectator sends `watch`; the server has the active bot *plan* the phase
  (`RandomBot.plan_*` stage orders through the engine's `submit_*`; Combat
  Resolution queues the battles about to be fought; the dice-free automatic
  phases queue a dry run of themselves) and sends a `phase_queue`. `next`
  commits the queue (`confirm_*` / `resolve_combat` / ...), sends
  `phase_result` (what executing logged: rolls and per-battle participants
  and casualties), a fresh `state` (so the map/HUD update after every phase)
  and the following `phase_queue`. Every faction's turn opens with a `START_OF_TURN`
  queue step (like `RETURN_TO_BASE`, a step of the queue rather than an engine
  phase): its `start_of_turn` event says which round and which turn is starting, and
  Settings' "Turn" pause now stops there. `--steps N` drives it in scripted runs (Combat Resolution is queued and fought one battle at a
  time.) A **Settings** button (top bar) opens playback options that decide
  when the client waits for Next instead of executing the queued phase
  itself: opponents' turns pause never / once per turn / every phase, plus
  optional pauses before each battle (or only battles involving your units),
  and before each battle on your own turn; the "yours" options are inert
  while there is no HUMAN faction. Unpaused phases run back to back with no
  added delay, waiting only for the previous phase's arrows to finish
  **Alliances (human):** the human's Alliances queue carries `human: {kind:
  "alliance", members, options {eligible_invite_targets, can_withdraw}, staged,
  game_would_end}`; the client picks the turn's one action -- do nothing, withdraw
  from the alliance, or invite one of the eligible factions (bots included) -- in the
  middle panel with `stage_alliance` (checked by a dry run of the engine call, so an
  invite that would break the alliance-size limit is refused at once), and
  holding the submit button executes it. An invited bot answers by its own alliance
  strategy when the phase runs (the queue does not reveal it); the result log says
  whether it joined or declined (`alliance_declined` is a new turn-log event). The
  other direction: when a BOT's Alliances phase invites the human, its queue carries
  an `invitation`, the client opens an invitation window (`InvitationWindow`: who
  invites, who is in the alliance, what it becomes -- it doesn't block the map, so
  you can look at the board first) and the phase cannot be executed until the player
  answers with `respond_invitation`; the answer is folded into the same queued plan.
  Withdrawing is disabled when the engine says so (a unit on an ally's Strategic
  Center). Bots invite only other bots or the human, since at most one human plays.
  **Launch screen:** the client opens on a launch screen (`LaunchScreen`); the server
  starts idle and a `GameHost` (`server/host.py`) builds the game the screen
  describes (`server/lobby.py`; `--demo` starts the old hardcoded NAA-vs-GPC game
  instead, for scripted runs). Six seats, each Human / Bot / Defense / Neutral;
  players (humans and bots) choose a faction (or random) and a starting alliance
  (none, 1, 2 or 3 -- seats sharing a number start allied, which needed a new
  `starting_alliances` option in `build_game_state`); bots also choose an alliance
  strategy and behavior (default random); "Randomize turn order" defaults on. Rules:
  at least two players, at most one human, each faction once, an alliance needs two
  or more members, can't be every player, and can't exceed the **Maximum alliance
  size** setting (default 3; choosable from 1 up to the number of players minus
  one; `max_alliance_size` in the `new_game` settings, validated by `server/lobby.py`;
  the engine still caps it by the number of factions still in play). **1 means no
  alliances**: the starting-alliance pickers are disabled and the Alliances phase is
  skipped outright (`GameState.alliances_enabled`; `server/stepper.py` commits it and
  goes straight to the next faction's turn, so no Alliances step is queued). Two
  more startup checkboxes, "Combat Moves allowed on a faction's first turn"
  (default off) and "Non-Combat Moves allowed on a faction's first turn" (default on),
  send `allow_combat_first_turn` / `allow_noncombat_first_turn`, which set the
  engine's `allow_combat_moves_first_turn` / `allow_noncombat_moves_first_turn`. A game can be seeded (`seed` in the `new_game` settings, `--seed` for the demo server and scripted client runs): setup, the bots and the dice all come from it, so the game replays exactly whatever Python's hash seed is (`server/tests/test_reproducible.py` checks that under different `PYTHONHASHSEED`s; the lobby used to leave the dice unseeded, which was the only source of variation). Every faction is in exactly one seat: explicit picks
  first, random seats take what is left; Defense seats use the 100-IPC setup, as
  before. Two more game settings, "Players can withdraw from alliances" (default yes)
  and "Players can rejoin alliances they left" (default no; greyed out when
  withdrawing is off) -- the engine has always enforced both
  (`can_withdraw_from_alliances` / `can_rejoin_alliances` on `GameState`: the legal
  options a human is offered, the dry-run check on `stage_alliance`, and the bots'
  own choices all follow them); the client shows them in the Alliances panel (a
  disabled Withdraw with the reason, and a "Can't invite X: ..." line for former
  allies or already-allied factions) and in the invitation window's wording. A
  two-member alliance now dissolves when either member withdraws (the other used to
  keep a one-member tag, which made it impossible to invite again, so rejoining
  could never work for a pair). The screen checks live, the server re-checks. Settings are remembered
  between launches; "New game..." in Settings returns to the screen (the running
  game stays until you start another, and "Resume" goes back to it).
  **Human players:** the demo server now runs NAA as a HUMAN and GPC as a bot
  (`python -m server.app --human none` for the old bots-only watch). A human
  faction's phases go through the same queue: its Purchase queue carries
  `engine.purchase_options` (treasury, each legal target's remaining capacity and
  whether the next unit is priced at a Strategic Center, staged orders with exact
  costs), and the client edits it with `stage_purchase` (the whole list; validated
  by `submit_purchases`, answered with a refreshed `phase_queue` or an error plus
  the unchanged queue); `next` confirms it, irreversibly. Its other phases stage
  nothing (combat/non-combat move UIs are not built), so they are passes. In the
  client the middle panel of the right column (`OrdersPanel`) is where orders are
  made: select a territory you control or an adjacent sea zone on the map, and it
  shows where units deploy (a sea zone is paid from, and limited by, its adjacent
  territories, Strategic Centers first), a row per unit type with its price and
  -/+ buttons, and the MCP budget (budget / queued / left). Queued purchases
  appear in the lower box, each with a [-] link to remove one, and as the darker
  box on the map. Submitting is a `HoldButton`: hold the mouse or Space for 1
  second, a ring fills around the button, and releasing early cancels.
  **Combat Move / Non-Combat Move:** the human queue carries `human: {kind,
  options, orders}` -- what each unit may still do with the staged moves applied
  (`engine.move_options_with_staged`, a throwaway copy where staged units have
  moved) and the staged moves themselves -- and the client edits it with
  `stage_moves` (the whole list, one order per unit: `path` for combat,
  `destination` for non-combat; validated by `submit_*_moves`). In the client,
  select a space with your units: its movable units start selected (click the
  faction banner to select/unselect all, or click units). `GameStore.move_targets`
  works out the highlighted spaces (green): every selected unit must reach the
  target, except that in an amphibious group (land + sea units) a land target only
  needs the land/air units to reach it, and the sea units that can escort them to
  the last sea zone of the landing path do. Air units in the space of a selected
  Aircraft Carrier ride along with it (the engine sweeps them, `carrier_ride_along`),
  so they are left out of that requirement (`GameStore.ride_along_ids`); an air
  unit selected alone flies on its own. Drag from the selected units (tile) or
  from the origin space on the map (dragging elsewhere still pans) onto a target:
  the arrow that will accompany the move follows the drag, and on release the
  move is queued. Committed units show dimmed with an arrow badge at the origin
  and as "Incoming" at the destination, and the queue lists each (from, to)
  group; any of the three lets you recall a unit, a group, or all incoming. The
  remaining units stay available. `python -m server.app --combat-first-turn`
  (dev) allows Combat Move on the first turn, which the rules skip by default (the
  launch screen has a checkbox for it now).
  shortening. While phases are running unpaused the Next button becomes a **Pause**
  button: it holds the phase in hand (or the next one, mid-execution) and
  offers Next as a scheduled pause would; after that Settings apply again. A battle that pauses because of the battle options (not an
  ordinary phase pause) opens the **battle board** (`reference/GC Battle Board
  Mockup.pdf`): the map first zooms to the territory and selects it (and returns to the previous view once Combat Resolution ends), the Next button then reads "Open battle board", and pressing it pops up a chart with both
  sides' units in the row of their **defense** value (5-10; no attack-die columns, units never move, except that a unit promoted
  mid-battle goes up a row at once), a Resolve setting per side (Entire Battle / Round / Side / Unit Type /
  Unit; default Unit Type, remembered) that sets how much one press (a "pulse") reveals, and with the finer ones (Side / Unit Type / Unit) a pause at the start of each side (a press that rolls nothing and says what that side is about to do; Round and Entire Battle run straight through),
  top-down dice per roll (each unit row's dice fill a grid in that row's half of the Roll column; a grid taller than the cell spills into the rows above and below, neighbours are pushed apart so no dice overlap, and a huge roll shrinks the dice to fit -- `_dice_layout`), `/` on hit units and `X` on eliminated ones (marked at
  the hit, as they still roll that round), XP shown the moment it is earned (promotions at round end), the first-round combat bonus named in the Ready/Round label with its side and reason, an empty-territory capture skipping the board, and a text box under the chart saying what the last pulse did (rolls, hits, units that had no legal target, round-end casualties and promotions) and what happens next, with the button labelled to match ("Start Defender's side", "Roll Attacker's Cruisers", ...). Every unit is brought back at the end, and the final End Battle state shows the battle summary at the bottom of that box. The dice are already fixed by the engine: the first Next Roll has the
  server fight the battle, and the client holds back the resulting state and log
  until End Battle. The engine supports it with per-round `UNIT_STATS` events, `SIDE_START` (a side with armed units is about to roll), `NO_TARGETS` (a unit with no legal target -- a Submarine against only aircraft, an aircraft against only Submarines -- does not roll and spends no die; the check is made as each unit's turn comes) and a `BATTLE_END` `end_reason` (`eliminated` / `no_targets` / `rounds`): a round is not fought at all when neither side has a legal target, which ends the battle; and
  richer `battle_preview` rows; land units afloat are Transports, and enemy Transports never block a move (combat or non-combat) or force a stop, though they can still be attacked (`movement._is_transport`); in a sea battle land units are Transport cargo
  (defense 6, 1 HP, no attack, no XP; sunk = lost). `BattleModel`
  (`client/scripts/battle_model.gd`) holds the stepping logic and is checked
  headlessly: `godot --headless --path client -s res://tests/battle_model_test.gd`. Saved in `user://settings.cfg`; `client_shot.py` takes
  `--pause never|turn|phase` and `--pause_battle`. Layout checks: `godot --headless --path client -s res://tests/battle_layout_test.gd`.
  Run: `python tools/sync_client_data.py` (copies reference data into the
  gitignored `client/data`, `client/assets`), `python -m server.app`, then
  `godot --path client -- --server`. Scripted UI verification without a
  human: `python tools/client_shot.py OUT.png [--server --steps N]
  [--cam x,y,z] [--select ID] [--wheel/--drag/--click ...]` runs the client,
  injects real input through the window, and saves a screenshot (see
  `client/scripts/dbg.gd`); the server keeps one game, so restart it
  between scripted runs.
