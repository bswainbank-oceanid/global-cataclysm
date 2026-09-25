"""
Legal-move computation: given a unit's current position, what territories/
sea zones can it legally reach this turn? Answers reachability queries
only -- it does not execute a move or apply consequences (capture,
becoming Transport cargo, the sea-battle-before-land-battle dependency
for an amphibious landing), which is engine.py's job. Every rule here
matches data/rules.json's movement section exactly; see that file for
the prose version of each rule this implements.

Alongside the destination-set queries (legal_combat_move_destinations,
legal_noncombat_move_destinations), the *_paths variants
(legal_combat_move_paths, legal_noncombat_move_paths) return the actual
route to each destination, not just the endpoint -- for any caller that
needs to construct a real move (CombatMoveOrder.path requires a full
route) rather than only check legality, e.g. a bot choosing among its
options or a future UI drawing the path a unit would take.
graph_distances is a separate, plain-adjacency BFS with no move-legality
awareness at all -- "how far is X from Y" in the abstract, for a bot
picking a general direction to advance in.

Sea units (category 'Sea') never leave the water at all: every search and
path validator here refuses a land hop for one, in both move phases -- it
can't end a move on land, pass over it, or attack it (see rules.json's
movement.sea_units_stay_at_sea).

Two move types, with different destination rules:
- combat: the destination becomes an attack (or joins one already in
  progress). Ends when the path enters non-allied or contested
  territory -- Mechanized Infantry is the sole exception, able to pass
  through (and capture) an EMPTY foreign land territory and keep going.
- noncombat: reposition only. Own or allied territory (contested or
  not), or any already-contested territory regardless of who's involved;
  never a clean (uncontested), non-allied foreign one. A sea zone
  occupied by a non-ally blocks non-combat movement -- that needs a
  combat move instead.

Alliances: FactionState.alliance is a plain membership tag (see
data/rules.json's alliances.status) that this module actively consults
throughout -- own-or-allied territory/units are treated as friendly
everywhere above, even though the alliance MECHANICS (joining/
withdrawing, betrayal) are still out of scope. Air's landing rule is the
one asymmetric case: allied LAND is fine (even contested), but landing
specifically requires the mover's OWN carrier, never an ally's.

Movement budget: a unit's own move stat, in both move types. A Transport gives no
move bonus, and every hop -- into the water, along it, and back onto land -- costs
one move.

Only an amphibious land unit (the amphibious ability: Mechanized
Infantry) may enter a sea zone at all. Infantry and Armor stay on land: every
search and path validator here refuses a water hop for them.

There's no such thing as a Transport moving under its own query here --
Transport isn't purchasable (the unit set) and has no independent
existence: it comes into being automatically when a Mechanized Infantry unit
enters water and disappears when that unit returns to land. It's purely a
combat-participation wrapper (see engine/combat.py, where it IS a real
target/attacker in a sea battle); nothing here ever calls legal_*_move_
destinations with unit_type='Transport'.
"""
from . import abilities
from .state import FactionMode, is_amphibious


def graph_distances(origin_id, data_module):
    """Pure adjacency-graph hop distance from origin_id to every other
    territory -- ignores move budget, ownership, and legality entirely.
    A general graph utility (not move/combat aware), for callers that
    need "how far is X from Y" in the abstract -- e.g. a bot deciding
    which direction to advance, or a future UI showing range rings.
    Plain BFS, unweighted (every edge is one hop)."""
    adjacency = data_module.adjacency()
    distances = {origin_id: 0}
    frontier = [origin_id]
    while frontier:
        next_frontier = []
        for current_id in frontier:
            for neighbor_id in adjacency.get(current_id, []):
                if neighbor_id in distances:
                    continue
                distances[neighbor_id] = distances[current_id] + 1
                next_frontier.append(neighbor_id)
        frontier = next_frontier
    return distances


def _base_move(unit_type, move_type, unit_defs):
    stats = unit_defs[unit_type]
    key = 'combat_move' if move_type == 'combat' else 'non_combat_move'
    return stats[key]


def _is_neutral(territory_id, game_state):
    owner = game_state.territories[territory_id].owner
    return owner is not None and game_state.factions[owner].mode == FactionMode.NEUTRAL


def _is_contested(territory_id, game_state):
    return bool(game_state.territories[territory_id].contested_by)


def _is_ally_or_self(game_state, mover_faction, other_faction):
    """True if `other_faction` is the mover itself or shares its
    alliance. Alliance mechanics (joining/withdrawing, betrayal) are
    still out of scope for v1 -- FactionState.alliance is a simple, already-
    set membership tag movement.py consults, not something this module
    manages."""
    if other_faction is None:
        return False
    if other_faction == mover_faction:
        return True
    mover_alliance = game_state.factions[mover_faction].alliance
    return mover_alliance is not None and mover_alliance == game_state.factions[other_faction].alliance


def _is_transport(unit, territory_id, territories, unit_defs):
    """A land unit in a sea zone IS a Transport (one per unit -- see
    rules.json water_movement_bonus_rule); there is no separate
    transport unit type on the board."""
    if abilities.has(unit_defs, unit.unit_type, abilities.TRANSPORT):
        return True
    return territories[territory_id]['type'] == 'sea' and unit_defs.get(unit.unit_type, {}).get('category') == 'Land'


def _enemies_present(territory_id, mover_faction, game_state, territories, unit_defs):
    """Combatants belonging to a faction that's neither the mover nor
    one of its allies -- Transports (land units afloat) never count as
    an occupying presence, per enemy_occupation_stop_rule: they neither
    block a non-combat move nor force a combat move to stop."""
    return any(not _is_ally_or_self(game_state, mover_faction, u.owner)
               and not _is_transport(u, territory_id, territories, unit_defs)
               for u in game_state.territories[territory_id].units)


def _enemy_transports_present(territory_id, mover_faction, game_state, territories, unit_defs):
    """True if a non-allied Transport is in the zone. They don't block
    anything, but they are still something a combat move can attack."""
    return any(not _is_ally_or_self(game_state, mover_faction, u.owner)
               and _is_transport(u, territory_id, territories, unit_defs)
               for u in game_state.territories[territory_id].units)


def _enemy_fighter_present(territory_id, mover_faction, game_state, unit_defs):
    """True if `territory_id` holds a unit with the interception ability (a
    Fighter) belonging to a faction that's neither `mover_faction` nor one of
    its allies -- the one thing that disrupts air's otherwise-unconstrained
    overflight of enemy and neutral territory (rules.json's
    air_interception_rule). Bombers, every other unit type, and simple
    non-ally/neutral presence alone never trigger this."""
    return any(abilities.has(unit_defs, u.unit_type, abilities.INTERCEPTION)
               and not _is_ally_or_self(game_state, mover_faction, u.owner)
               for u in game_state.territories[territory_id].units)


class _Hop:
    """Classification of a single step in a combat-move path onto
    `dest_id`, for `mover_faction`/`unit_type`. `stop`: legal as a final
    destination for this move. `pass_through`: None (can't continue),
    'any' (continue onto any legal neighbor), or 'land_only' (continue,
    but only onto a land neighbor -- the amphibious-landing-through-
    occupied-water case: a land unit in transit may fight through an
    occupied sea zone straight onto adjacent land, but not chain onward
    into further open sea)."""
    __slots__ = ('stop', 'pass_through')

    def __init__(self, stop, pass_through=None):
        self.stop = stop
        self.pass_through = pass_through


BLOCKED = _Hop(stop=False)
PASS_ONLY = _Hop(stop=False, pass_through='any')
STOP_ONLY = _Hop(stop=True)
STOP_AND_PASS = _Hop(stop=True, pass_through='any')
STOP_AND_PASS_LAND_ONLY = _Hop(stop=True, pass_through='land_only')


def _classify_combat_hop(dest_id, mover_faction, unit_type, is_land_unit, game_state, territories, unit_defs):
    if _is_neutral(dest_id, game_state):
        return BLOCKED

    dest = game_state.territories[dest_id]
    is_land = territories[dest_id]['type'] == 'land'
    contested = _is_contested(dest_id, game_state)
    own_or_ally_land = is_land and _is_ally_or_self(game_state, mover_faction, dest.owner)

    if not is_land and is_land_unit and (contested or _enemies_present(dest_id, mover_faction, game_state, territories, unit_defs)):
        # Hostile (occupied OR contested) sea zone, land unit currently
        # in transit (i.e. riding a Transport): must be prepared to
        # fight/rejoin the naval battle here, but may continue straight
        # on to adjacent LAND in the same move -- to attack, join an
        # ongoing fight, or land safely on friendly (own or allied)
        # territory to escape the hostile water. Landing there is
        # always a legal stop for this move regardless of whose land it
        # is (see the BFS driver, which enforces that even though the
        # ordinary classification of friendly land below wouldn't
        # otherwise mark it as one) -- still a combat move, not a
        # non-combat one, since it passed through contested/occupied
        # water to get there. Actually completing the crossing is
        # contingent on surviving that sea zone's naval battle first
        # (see combat.battle_resolution_pass_order), an execution-time
        # dependency, not a reachability concern.
        return STOP_AND_PASS_LAND_ONLY

    if own_or_ally_land:
        # Own or allied territory -- freely transitable regardless of
        # contested status (checked before the generic contested check
        # below, so this doesn't get swallowed by it); attacking your
        # own or an ally's land isn't a thing. But when it is contested
        # (someone is fighting for it), a combat move may end here to
        # join that fight -- and still pass on through, like any own land.
        return STOP_AND_PASS if contested else PASS_ONLY

    if _enemies_present(dest_id, mover_faction, game_state, territories, unit_defs):
        # Joining or continuing a fight already in progress (or a fresh
        # attack on an occupied foreign territory) -- a legal place to
        # end a combat move, but combat move must result in an attack,
        # not walk past one; never a pass-through. Deliberately NOT the
        # plain `contested` flag on its own (a bug found and fixed this
        # session): GameEngine._mark_contested_by_attack adds a LAND
        # territory's registered owner to contested_by on ANY entry,
        # even an entirely undefended Mechanized Infantry blitz through
        # empty land -- so `contested` alone stayed true (and wrongly
        # forced a stop) for a SECOND Mechanized Infantry of the SAME
        # faction, same batch, wanting to blitz through that same
        # now-self-marked-but-still-EMPTY territory to a different
        # further destination, with nobody actually there to fight or
        # dispute the claim. Physical presence is the real signal here;
        # a genuine multi-round stalemate (rules.json's 3-round cap)
        # only ever leaves a territory contested WITH both sides' units
        # still on it, so this never misses an actual fight.
        return STOP_ONLY  # occupied foreign territory (or occupied sea for a non-land unit): attack, stop here

    # Empty (no defenders currently present -- contested_by may still
    # name this land territory's owner from an earlier entry this same
    # batch, but nobody is actually here to fight or dispute it). For
    # sea zones this is just ordinary open water -- always pass-through,
    # no ownership/capture concept applies to sea. For land, it's a
    # foreign territory nobody is defending: capturable, and only
    # Mechanized Infantry may continue past it in the same move.
    if not is_land:
        # Open sea, or sea holding only enemy Transports: never blocks, but the
        # Transports can be attacked by ending the move here.
        if _enemy_transports_present(dest_id, mover_faction, game_state, territories, unit_defs):
            return STOP_AND_PASS
        return PASS_ONLY
    if abilities.has(unit_defs, unit_type, abilities.BLITZ):
        return STOP_AND_PASS
    return STOP_ONLY


def _classify_noncombat_hop(dest_id, mover_faction, game_state, territories, unit_defs):
    """Noncombat move: own or allied land (contested or not), any
    territory already contested (regardless of who owns it or who's
    contesting it -- unqualified), or open/allied-occupied sea. Never a
    clean foreign (non-allied) land territory, and never a sea zone
    occupied by a non-ally -- that would require a combat move instead.
    No attack semantics, no Mech Inf exception (that's combat-move-only);
    only amphibious land units may enter water (the caller's search
    enforces that, not this classification)."""
    if _is_neutral(dest_id, game_state):
        return BLOCKED
    dest = game_state.territories[dest_id]
    is_land = territories[dest_id]['type'] == 'land'
    if is_land:
        if _is_ally_or_self(game_state, mover_faction, dest.owner) or _is_contested(dest_id, game_state):
            return STOP_AND_PASS
        return BLOCKED  # clean, non-allied foreign land
    if _enemies_present(dest_id, mover_faction, game_state, territories, unit_defs):
        return BLOCKED  # non-ally-occupied water -- would need to be a combat move
    return STOP_AND_PASS


def _reachable_destinations(origin_id, mover_faction, unit_type, move_type, game_state, data_module,
                             with_paths=False, budget_override=None):
    """Core BFS shared by combat/noncombat reachability. Tracks, per
    territory, the best (largest) remaining-budget-on-arrival seen so
    far, and only explores a neighbor when arriving with a strictly
    better remaining budget than any prior visit -- since budget only
    decreases per hop, this
    naturally terminates without needing a separate depth cap. (Known
    simplification: this pruning is budget-only, not budget-plus-
    land_only-restriction -- a node reached both via an unrestricted
    path and, with a better budget, via a land-only-restricted one could
    in principle have the unrestricted path's further options pruned
    away. Narrow edge case, and fails conservatively -- toward missing a
    legal destination, never toward allowing an illegal one.)

    with_paths: if True, also reconstructs the actual route to each
    destination (from the same parent pointers the pruning above already
    needs to maintain) and returns (destinations, {destination:
    [origin_id, ..., destination]}) instead of just destinations. Ties
    (more than one equal-length legal route) resolve to whichever path
    the traversal happened to accept last -- any one of them is an
    equally legal route, since _classify_combat_hop/_classify_noncombat_hop
    were satisfied at every hop along it.

    budget_override: search with this many moves available instead of
    unit_type's own move stat -- for legal_combat_move_continuations,
    which resumes a search from a point already reached, with whatever
    budget is left over from the hop(s) already spent getting there. None
    (the default) means "the unit's full move stat," as if starting a
    fresh move from origin_id, unchanged for every other caller."""
    unit_defs = data_module.units()
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    is_land_unit = unit_defs[unit_type]['category'] == 'Land'
    is_sea_unit = unit_defs[unit_type]['category'] == 'Sea'

    base_budget = _base_move(unit_type, move_type, unit_defs) if budget_override is None else budget_override
    started_in_water = is_land_unit and territories[origin_id]['type'] == 'sea'
    can_swim = is_land_unit and is_amphibious(unit_defs[unit_type])

    destinations = set()
    paths = {}
    best_seen = {origin_id: base_budget}
    # stack entries: (territory_id, moves_used_so_far, water_bonus_active,
    # land_only_restricted, path_so_far). path_so_far is carried directly
    # per entry -- not reconstructed afterward from a shared parent map --
    # because best_seen's budget-only pruning (see this function's own
    # docstring) means a node can be VISITED again later via a cheaper,
    # merely-passing-through route after it already qualified as a
    # destination via a costlier one; a shared parent map would let that
    # later visit silently overwrite the route that actually justified
    # the earlier stop, producing a reconstructed path trace_combat_move
    # would then reject as illegal (a bug found and fixed this session --
    # legal_combat_move_paths could return a destination alongside a
    # path that doesn't actually reach it legally). Carrying the path
    # per stack entry instead means whatever gets recorded into `paths`
    # is always the exact walk that was live at the moment the stop was
    # confirmed, so it's always genuinely legal, even if not always the
    # shortest of several equal-length options.
    stack = [(origin_id, 0, started_in_water, False, [origin_id])]

    while stack:
        current_id, moves_used, water_active, land_only, current_path = stack.pop()
        for neighbor_id in adjacency.get(current_id, []):
            neighbor_is_land = territories[neighbor_id]['type'] == 'land'
            if is_sea_unit and neighbor_is_land:
                continue  # movement.sea_units_stay_at_sea: never enter, cross, or attack land
            if land_only and not neighbor_is_land:
                continue  # amphibious continuation past occupied water: land only, never further open sea
            if is_land_unit and not neighbor_is_land and not can_swim:
                continue  # only amphibious land units (Mechanized Infantry) may enter the water
            new_water_active = water_active or (is_land_unit and not neighbor_is_land)
            new_moves_used = moves_used + 1
            if new_moves_used > base_budget:
                continue
            remaining = base_budget - new_moves_used
            if remaining <= best_seen.get(neighbor_id, -1):
                continue  # already reached this territory with an equal-or-better remaining budget
            best_seen[neighbor_id] = remaining
            new_path = current_path + [neighbor_id]

            if move_type == 'combat':
                hop = _classify_combat_hop(neighbor_id, mover_faction, unit_type, is_land_unit, game_state, territories, unit_defs)
            else:
                hop = _classify_noncombat_hop(neighbor_id, mover_faction, game_state, territories, unit_defs)
            # Landing on this neighbor via the hostile-water escape/
            # amphibious exception is always a legal stop, even when the
            # neighbor's own ordinary classification wouldn't otherwise
            # mark it as one (e.g. friendly uncontested land, which is
            # normally pass-through-only) -- it's still a combat move,
            # just one that ends in a safe landing rather than an attack.
            # NEUTRAL is the one exception even to that override -- it's
            # never enterable at all (movement.neutral_exclusion), so the
            # override only applies when the hop wasn't neutral-blocked
            # (a bug found and fixed this session: `hop.stop or (land_only
            # and neighbor_is_land)` used to bypass _is_neutral entirely).
            # That landing is also ALWAYS the final stop of the move --
            # never continuable, even when the landing spot's own
            # ordinary classification would otherwise permit passing
            # through it (e.g. Mechanized Infantry landing on an empty
            # foreign territory, which would normally let it blitz
            # onward) -- so it's never pushed onward here either, a
            # second bug found and fixed this session alongside the
            # Neutral one, matching trace_combat_move's own
            # (already-correct) 'must be the final stop' check.
            if land_only:
                if neighbor_is_land and not _is_neutral(neighbor_id, game_state):
                    destinations.add(neighbor_id)
                    paths[neighbor_id] = new_path
                continue
            if hop.stop:
                destinations.add(neighbor_id)
                paths[neighbor_id] = new_path
            if hop.pass_through:
                stack.append((neighbor_id, new_moves_used, new_water_active, hop.pass_through == 'land_only', new_path))

    if not with_paths:
        return destinations
    return destinations, paths


def _legal_bombardment_targets(unit_type, owner, origin_id, game_state, data_module):
    """{target_land_id: [origin_id, (sea_hop,) target_land_id]} -- rules.json's
    cruiser_bombardment: every enemy-occupied land territory reachable from
    origin_id by a Cruiser that either bombards from right where it is, or
    repositions ONE sea hop first -- never both, and that one hop must be a
    legal, non-combative move (open water, or a sea zone holding only enemy
    Transports, exactly like an ordinary PASS-type combat-move hop) -- a
    Cruiser can bombard or fight a naval battle this turn, never both, so
    the repositioning hop itself can never legally BE an attack. The
    notional final hop onto the land target is never actually taken (see
    movement.trace_combat_move's BombardmentTrace and engine.engine.
    GameEngine._execute_combat_moves, which leave the unit at the path's
    last REAL, i.e. sea, entry). A target reachable both directly and via
    one hop keeps its direct (shorter) path."""
    unit_defs = data_module.units()
    territories = data_module.territories()
    adjacency = data_module.adjacency()

    def enemy_occupied_land_neighbors(sea_id):
        out = []
        for n in adjacency.get(sea_id, []):
            if territories[n]['type'] != 'land' or _is_neutral(n, game_state):
                continue
            if any(not _is_ally_or_self(game_state, owner, u.owner) for u in game_state.territories[n].units):
                out.append(n)
        return out

    targets = {}
    for t in enemy_occupied_land_neighbors(origin_id):
        targets[t] = [origin_id, t]
    for sea_id in adjacency.get(origin_id, []):
        if territories[sea_id]['type'] != 'sea':
            continue
        hop = _classify_combat_hop(sea_id, owner, unit_type, False, game_state, territories, unit_defs)
        if not hop.pass_through:
            continue  # would be a naval attack, not a repositioning move -- illegal while bombarding
        for t in enemy_occupied_land_neighbors(sea_id):
            if t not in targets:
                targets[t] = [origin_id, sea_id, t]
    return targets


def legal_combat_move_destinations(unit_type, owner, origin_id, game_state, data_module):
    """Territories/sea zones `owner`'s `unit_type` unit, currently at
    `origin_id`, could legally end a combat move at. Air units should
    use legal_air_move_destinations instead (they ignore occupation
    entirely and aren't subject to the land/sea pass-through rules
    here). For a Cruiser, also includes every legal bombardment target
    (_legal_bombardment_targets) -- a land destination it can declare, but
    never actually enters."""
    dest = _reachable_destinations(origin_id, owner, unit_type, 'combat', game_state, data_module)
    if abilities.has(data_module.units(), unit_type, abilities.BOMBARDMENT):
        dest = dest | set(_legal_bombardment_targets(unit_type, owner, origin_id, game_state, data_module))
    return dest


def legal_combat_move_paths(unit_type, owner, origin_id, game_state, data_module):
    """Like legal_combat_move_destinations, but returns {destination_id:
    [origin_id, ..., destination_id]} -- the actual route to each legal
    destination, not just which ones are reachable. Needed anywhere a
    caller must actually construct a move (CombatMoveOrder.path requires
    the full route, not just an endpoint) rather than only check
    legality -- a bot choosing among its options, or a future interactive
    UI drawing the route a unit would take. For a Cruiser, also includes
    every legal bombardment target's path (_legal_bombardment_targets)."""
    _, paths = _reachable_destinations(origin_id, owner, unit_type, 'combat', game_state, data_module, with_paths=True)
    if abilities.has(data_module.units(), unit_type, abilities.BOMBARDMENT):
        paths = dict(paths)
        paths.update(_legal_bombardment_targets(unit_type, owner, origin_id, game_state, data_module))
    return paths


def legal_combat_move_continuations(unit_type, owner, origin_id, game_state, data_module):
    """{first_hop_id: {destination_id: [origin_id, first_hop_id, ...,
    destination_id]}} -- for every one-hop neighbor of origin_id that's a
    legal PASS-THROUGH stop (_classify_combat_hop's STOP_AND_PASS: an
    empty, capturable foreign land territory for a Mechanized Infantry,
    or an open/enemy-Transport-only sea zone -- never STOP_AND_PASS_LAND_
    ONLY, the hostile-water-escape landing, which is always the final
    stop of a move and never continuable), the further destinations
    reachable by continuing on from there with whatever move budget is
    left over after that first hop, each with its own full path back to
    origin_id. {} for a first hop that isn't pass-through-capable, that
    has no budget left afterward, or that has nowhere further to go.

    legal_combat_move_paths' own single BFS from origin_id already picks
    ONE canonical path to each reachable destination (the one its
    traversal order happened to settle on -- see _reachable_destinations'
    own docstring on how ties resolve) -- it doesn't surface that a
    different, equally legal route existed through a different first
    hop. This is what lets a human client offer that choice explicitly:
    stage a one-hop combat move that didn't itself trigger a battle, then
    -- knowing the specific further destinations reachable from exactly
    that stop -- offer a second, explicit hop from there, picking a route
    hop by hop rather than only the one path the single-shot search
    happens to return for a given final destination."""
    unit_defs = data_module.units()
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    is_land_unit = unit_defs[unit_type]['category'] == 'Land'
    is_sea_unit = unit_defs[unit_type]['category'] == 'Sea'
    can_swim = is_land_unit and is_amphibious(unit_defs[unit_type])
    base_budget = _base_move(unit_type, 'combat', unit_defs)
    if base_budget < 2:
        return {}

    out = {}
    for first_hop_id in adjacency.get(origin_id, []):
        neighbor_is_land = territories[first_hop_id]['type'] == 'land'
        if is_sea_unit and neighbor_is_land:
            continue  # movement.sea_units_stay_at_sea
        if is_land_unit and not neighbor_is_land and not can_swim:
            continue  # only an amphibious land unit may enter water at all
        hop = _classify_combat_hop(first_hop_id, owner, unit_type, is_land_unit, game_state, territories, unit_defs)
        if not (hop.stop and hop.pass_through == 'any'):
            continue  # not a legal stop here at all, or one that's always the final stop (an attack, or a hostile-water-escape landing)
        destinations, paths = _reachable_destinations(
            first_hop_id, owner, unit_type, 'combat', game_state, data_module,
            with_paths=True, budget_override=base_budget - 1)
        if not destinations:
            continue
        out[first_hop_id] = {dest: [origin_id] + paths[dest] for dest in destinations}
    return out


class CombatMoveTrace:
    """Result of trace_combat_move: which LAND territories were entered
    via an uncontested Mechanized-Infantry blitz pass-through
    (entered_en_route -- never includes the final stop), and what kind
    of consequence the final stop represents (final_kind):
    'capture' (empty foreign land, uncontested), 'attack' (enemy-
    occupied), 'join_contest' (already contested), or 'safe_landing'
    (friendly land reached via a hostile-water escape, or -- for a naval
    unit -- simply contested/enemy water, which for a non-land unit is
    always an attack/join, never a "landing").

    Every territory entered or passed through this way -- both
    entered_en_route and a 'capture'/'attack'/'join_contest' final stop
    -- is marked CONTESTED by the caller (engine.py), never given
    immediate ownership: even an entirely undefended Mechanized Infantry
    blitz doesn't capture outright the moment it passes through. Actual
    ownership is resolved later, in the Capture Territory phase, from
    whatever the board looks like by then (see rules.json's
    movement.combat_move_destination and turn_order's Capture Territory
    entry) -- 'capture' here is just a label distinguishing "nobody was
    defending it" from 'attack', not a claim that ownership already
    changed.

    crossed_water: True for a LAND unit whose path touched a sea zone
    anywhere -- as its origin, an intermediate hop, or even the final
    stop -- i.e. it rode a Transport for at least part of this move.
    Always False for a sea unit (moving through open water is just
    normal movement for it, not a meaningful "crossing"). Feeds
    combat.first_round_bonuses' amphibious-landing check
    (engine.engine.GameEngine._execute_combat_moves stamps this onto
    UnitInstance.arrived_amphibiously for Land-category units) -- only
    meaningful there when the unit's FINAL stop is land (one that ends
    in open water instead is a naval combatant, never a land-battle
    attacker, so a True value here for that case is harmless)."""
    __slots__ = ('entered_en_route', 'final_kind', 'crossed_water')

    def __init__(self, entered_en_route, final_kind, crossed_water):
        self.entered_en_route = entered_en_route
        self.final_kind = final_kind
        self.crossed_water = crossed_water


class BombardmentTrace:
    """Result of trace_combat_move for a Cruiser's bombardment declaration
    (rules.json's combat.cruiser_bombardment): the Cruiser never enters
    target_id (a land territory) -- it stays at final_sea_id, the last
    REAL position in its path (its origin, or the one sea zone it
    repositioned to). engine.engine.GameEngine._execute_combat_moves reads
    this instead of relocating the unit onto land; GameEngine._resolve_
    bombardments (at the start of Combat Resolution) is what actually
    rolls the attack -- this only validates the declaration itself."""
    __slots__ = ('final_sea_id', 'target_id')

    def __init__(self, final_sea_id, target_id):
        self.final_sea_id = final_sea_id
        self.target_id = target_id


def _trace_bombardment_movement(unit_type, owner, path, game_state, data_module):
    """The movement half of a bombardment-shaped path (path[-1] is land):
    at most one real sea hop -- open, non-combative water only, exactly
    like _legal_bombardment_targets computes -- then a final, notional hop
    onto the target, which is never actually entered. Returns (final_sea_id,
    target_id). Raises ValueError if the path isn't exactly this shape or
    the sea leg isn't legal.

    Validates ONLY the movement -- not whether `owner`'s `unit_type` unit is
    actually allowed to end up bombarding/escorting at target_id, which is a
    different rule for a Cruiser declaring a fresh bombardment (rules.json's
    combat.cruiser_bombardment: enemy units must actually be present) than
    for another Sea unit riding along a sibling Cruiser's bombardment this
    same turn (engine.engine.GameEngine._execute_combat_moves: legal only
    when a Cruiser of the same faction is already bombarding that exact
    target this same order batch) -- see _trace_bombardment for the former;
    the latter is batch-aware and lives in engine.py instead, since this
    module's functions only ever see one order at a time."""
    if len(path) > 3:
        raise ValueError(f'{unit_type} may reposition at most one sea zone before bombarding')
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    unit_defs = data_module.units()
    target_id = path[-1]

    if len(path) == 3:
        origin_id, sea_id = path[0], path[1]
        if sea_id not in adjacency.get(origin_id, []):
            raise ValueError(f'{origin_id} and {sea_id} are not adjacent')
        if territories[sea_id]['type'] != 'sea':
            raise ValueError(f'{sea_id} is not a sea zone')
        hop = _classify_combat_hop(sea_id, owner, unit_type, False, game_state, territories, unit_defs)
        if not hop.pass_through:
            raise ValueError(f'{sea_id} is not a legal repositioning move (it would be a naval attack, not a bombardment)')
    else:
        sea_id = path[0]

    if target_id not in adjacency.get(sea_id, []):
        raise ValueError(f'{sea_id} and {target_id} are not adjacent')
    if territories[target_id]['type'] != 'land':
        raise ValueError(f'{target_id} is not a land territory')
    if _is_neutral(target_id, game_state):
        raise ValueError(f'{target_id} is neutral territory and cannot be bombarded')

    return sea_id, target_id


def _trace_bombardment(unit_type, owner, path, game_state, data_module):
    """A Cruiser's own combat move whose final hop targets enemy-occupied
    land: rules.json's combat.cruiser_bombardment -- see
    _trace_bombardment_movement for the shared repositioning rules. Adds
    the Cruiser-specific eligibility check on top: there must actually be
    enemy units present to bombard. Raises ValueError if either check
    fails."""
    final_sea_id, target_id = _trace_bombardment_movement(unit_type, owner, path, game_state, data_module)
    defenders = game_state.territories[target_id].units
    if not any(not _is_ally_or_self(game_state, owner, u.owner) for u in defenders):
        raise ValueError(f'{target_id} has no enemy units to bombard')
    return BombardmentTrace(final_sea_id=final_sea_id, target_id=target_id)


def trace_combat_move(unit_type, owner, path, game_state, data_module):
    """Validates `path` (a list of territory_ids: path[0] is where the
    unit currently is, path[-1] the chosen final destination, every
    consecutive pair adjacent) as a fully legal combat move for a LAND
    or SEA `unit_type` -- air units have no hop-by-hop legality or
    capture concerns and should use legal_air_move_destinations instead.
    Unlike legal_combat_move_destinations (which BFS-explores every
    reachable destination but never records HOW it got there), this
    walks the ONE given path, hop by hop, using the exact same
    _classify_combat_hop rules -- necessary because Mechanized
    Infantry's blitz can legally capture more than one empty foreign
    territory in a single move, and knowing WHICH ones requires the
    actual route, not just the endpoint (a unit can have more than one
    equal-length legal route to the same destination).

    Raises ValueError with a specific reason if any hop is illegal or
    the path exceeds the unit's combat-move budget (including the
    dynamic water-crossing bonus, applied the same way
    _reachable_destinations does). Returns a CombatMoveTrace on success --
    or, for a Cruiser whose path's final entry is land, a BombardmentTrace
    instead (delegated to _trace_bombardment, an entirely different, much
    narrower set of rules -- see that function and rules.json's
    combat.cruiser_bombardment).
    """
    if len(path) < 2:
        raise ValueError('a combat move path needs at least an origin and a destination')

    territories = data_module.territories()
    if abilities.has(data_module.units(), unit_type, abilities.BOMBARDMENT) and territories[path[-1]]['type'] == 'land':
        return _trace_bombardment(unit_type, owner, path, game_state, data_module)

    unit_defs = data_module.units()
    adjacency = data_module.adjacency()
    is_land_unit = unit_defs[unit_type]['category'] == 'Land'
    is_sea_unit = unit_defs[unit_type]['category'] == 'Sea'

    base_budget = _base_move(unit_type, 'combat', unit_defs)
    water_active = is_land_unit and territories[path[0]]['type'] == 'sea'
    moves_used = 0
    entered_en_route = []
    # True when the PREVIOUS hop was a hostile-water crossing (_Hop's
    # 'land_only' pass-through) -- forces THIS hop to be land, and to be
    # the final stop of the whole path (see the amphibious-landing
    # comment below). Mirrors _reachable_destinations' BFS driver, which
    # carries this same flag forward from one hop to the next rather
    # than checking it against the hop that set it.
    land_only_restricted = False

    for i in range(1, len(path)):
        current_id, next_id = path[i - 1], path[i]
        if next_id not in adjacency.get(current_id, []):
            raise ValueError(f'{current_id} and {next_id} are not adjacent')
        neighbor_is_land = territories[next_id]['type'] == 'land'
        is_last = (i == len(path) - 1)
        if is_sea_unit and neighbor_is_land:
            raise ValueError(f'{unit_type} is a sea unit and cannot enter land territory {next_id}')

        if land_only_restricted:
            if not neighbor_is_land:
                raise ValueError(f'cannot continue past hostile water without landing at {next_id}')
            if not is_last:
                raise ValueError('a hostile-water landing must be the final stop of this combat move')
            if _is_neutral(next_id, game_state):
                # The amphibious exception overrides ordinary land
                # classification (own/ally land's normal pass-only
                # status, in particular), but never NEUTRAL -- that's
                # still an absolute block, same as everywhere else (a
                # bug found and fixed this session: this hop used to
                # skip _classify_combat_hop -- and so its _is_neutral
                # check -- entirely once land_only_restricted was set).
                raise ValueError(f'{next_id} is neutral territory and cannot be entered')

        if is_land_unit and not neighbor_is_land and not is_amphibious(unit_defs[unit_type]):
            raise ValueError(f'{unit_type} cannot enter the water ({next_id}); only Mechanized Infantry can')
        water_active = water_active or (is_land_unit and not neighbor_is_land)
        moves_used += 1
        if moves_used > base_budget:
            raise ValueError(f"path exceeds {unit_type}'s combat move budget ({base_budget})")

        if land_only_restricted:
            # Landing forced by the amphibious exception -- always a
            # legal stop here regardless of this land territory's own
            # ordinary classification (e.g. even normally-pass-only
            # friendly land); final_kind below, derived from raw state,
            # works out whether it's an attack, a capture, joining a
            # fight, or a safe landing. Never itself a "capture along
            # the way" in entered_en_route's sense -- it's already the
            # final hop (enforced above), so any entry here shows up
            # as final_kind == 'capture' instead.
            land_only_restricted = False
            continue

        hop = _classify_combat_hop(next_id, owner, unit_type, is_land_unit, game_state, territories, unit_defs)
        if is_last:
            if not hop.stop:
                raise ValueError(f'{next_id} is not a legal place to end this combat move')
        else:
            if not hop.pass_through:
                raise ValueError(f'{unit_type} cannot continue past {next_id}')
            if hop.stop and neighbor_is_land and not _is_ally_or_self(game_state, owner, game_state.territories[next_id].owner):
                # The only way a mid-path hop is BOTH a legal stop and
                # continuable is Mechanized Infantry's empty-foreign-land
                # blitz (STOP_AND_PASS) -- own/allied land is pass-only
                # (never "stop"), and a contested/enemy-occupied hop is
                # stop-only (never continuable), so reaching here always
                # means an uncontested entry along the way -- marked
                # contested by the caller, same as everything else here.
                entered_en_route.append(next_id)
        land_only_restricted = (hop.pass_through == 'land_only')

    final_id = path[-1]
    final_state = game_state.territories[final_id]
    final_is_land = territories[final_id]['type'] == 'land'
    if final_is_land and _is_ally_or_self(game_state, owner, final_state.owner) and not final_state.contested_by:
        final_kind = 'safe_landing'
    elif final_state.contested_by:
        final_kind = 'join_contest'
    elif _enemies_present(final_id, owner, game_state, territories, unit_defs):
        final_kind = 'attack'
    elif final_is_land:
        final_kind = 'capture'
    else:
        final_kind = 'attack'  # empty open sea is never a legal final stop -- see _classify_combat_hop

    return CombatMoveTrace(entered_en_route=entered_en_route, final_kind=final_kind, crossed_water=water_active)


def legal_noncombat_move_destinations(unit_type, owner, origin_id, game_state, data_module):
    """Territories/sea zones `owner`'s `unit_type` unit, currently at
    `origin_id`, could legally end a non-combat move at."""
    return _reachable_destinations(origin_id, owner, unit_type, 'noncombat', game_state, data_module)


def legal_noncombat_move_paths(unit_type, owner, origin_id, game_state, data_module):
    """Like legal_noncombat_move_destinations, but returns {destination_id:
    [origin_id, ..., destination_id]}. A non-combat move never captures
    anything en route, so unlike the combat-move case the path itself
    has no rules consequence -- this exists purely for callers (a bot, a
    UI) that want to show/pick a concrete route rather than just an
    endpoint."""
    _, paths = _reachable_destinations(origin_id, owner, unit_type, 'noncombat', game_state, data_module, with_paths=True)
    return paths


def legal_air_move_destinations(unit_type, owner, origin_id, move_type, game_state, data_module):
    """Air units fly over enemy AND neutral territories/sea zones freely
    (never blocked, never forced to stop by mere occupation) in both
    move phases -- the only constraints are their own move budget (no
    water bonus; that's a land-unit/Transport concept), an enemy
    Fighter specifically disrupting that overflight (see below), and
    where they're allowed to end the turn:
    - combat: must be a real attack target -- non-ally-occupied, or
      already contested (joining the fight). Own/allied territory isn't
      an attack, and neither is empty foreign territory, since air alone
      can't capture (per the turn-order rule) -- there's nothing there
      to actually attack.
    - noncombat: own-or-allied land, contested or not -- landing there
      doesn't care about contested status either way. A sea zone
      requires the mover's OWN Aircraft Carrier -- never an ally's, and
      that's unconditional too (regardless of contested status) --
      already present OR queued to deploy there this turn. Landing on
      open water with no own carrier there yet and none pending is never
      legal, even though one COULD show up later the
      same phase via its own move order; movement.py only ever evaluates
      one unit's move in isolation and can't see, and per this rule
      shouldn't guess at, another unit's not-yet-submitted move.

    Enemy Fighter interception (rules.json's air_interception_rule):
    Bombers, ground/sea units, and simple non-ally/neutral presence
    never disrupt overflight -- ONLY an enemy Fighter (belonging to a
    faction that's neither `owner` nor one of its allies) does, in
    whichever territory/sea zone it's physically sitting in along the
    path. Where one is present, that node becomes a hard boundary: for
    a combat move, the flight is forced to stop there and attack (it's
    always a valid attack target once a Fighter is there, since that
    already means _enemies_present) and can't continue past it to a
    further destination in the same move -- same consequence
    enemy_occupation_stop_rule gives land/sea units, just narrower in
    what triggers it. For a non-combat move, that node is entirely off
    limits -- neither a legal landing spot nor a legal pass-through hop.
    Not applied by process_return_to_base's automatic snap-back (a
    direct return to a recorded origin, not a fresh pathfinding move --
    there's no path to intercept)."""
    unit_defs = data_module.units()
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    budget = _base_move(unit_type, move_type, unit_defs)

    reachable = set()
    best_seen = {origin_id: budget}
    stack = [(origin_id, 0)]
    while stack:
        current_id, moves_used = stack.pop()
        for neighbor_id in adjacency.get(current_id, []):
            new_moves_used = moves_used + 1
            if new_moves_used > budget:
                continue
            remaining = budget - new_moves_used
            if remaining <= best_seen.get(neighbor_id, -1):
                continue
            best_seen[neighbor_id] = remaining
            intercepted = _enemy_fighter_present(neighbor_id, owner, game_state, unit_defs)
            if intercepted and move_type == 'noncombat':
                continue  # off limits entirely: neither a landing spot nor a pass-through hop
            reachable.add(neighbor_id)
            if intercepted:
                continue  # combat: forced to stop and attack here, can't fly on past it
            stack.append((neighbor_id, new_moves_used))

    if move_type == 'noncombat':
        def legal_landing(tid):
            dest = game_state.territories[tid]
            if territories[tid]['type'] == 'land':
                # Own or allied land, contested or not -- confirmed this
                # session: allied land is fine to land on even while
                # contested, same as it's always been. The real
                # restriction is the sea/carrier one below (a contested,
                # enemy-owned/occupied sea zone needs your own carrier,
                # not just any presence) -- land was never actually part
                # of that restriction.
                return _is_ally_or_self(game_state, owner, dest.owner)
            # Sea: illegal outright if occupied by a non-ally (that
            # needs a combat move instead, same as ground units).
            # Otherwise legal only if the mover's OWN (never an ally's --
            # unlike land, landing specifically requires your own
            # carrier) Aircraft Carrier is either already sitting there,
            # or already queued to deploy there this turn
            # (TerritoryState.pending_deployment -- a real, GameState-
            # visible fact, unlike another unit's own move order, which
            # hasn't happened yet and might not). Landing on open water
            # with no carrier of ANY kind present, or one that's only an
            # ally's, is illegal even though your own carrier COULD
            # arrive there later the same phase via its own move:
            # movement.py only ever evaluates one unit's move in
            # isolation, so it has no way to know, and per this rule
            # shouldn't try to guess, what order a player will submit
            # the rest of their moves in.
            if _enemies_present(tid, owner, game_state, territories, unit_defs):
                return False
            own_carrier = lambda u: abilities.has(unit_defs, u.unit_type, abilities.CARRIER_AIR_WING) and u.owner == owner
            return any(own_carrier(u) for u in dest.units) or any(own_carrier(u) for u in dest.pending_deployment)
        return {tid for tid in reachable if legal_landing(tid)}

    # combat: must result in an attack, same as any other combat move --
    # a non-ally-occupied territory (a real attack), or occupied sea (an
    # enemy Transport, even alone). Own/allied/empty-foreign territory is
    # excluded: landing there isn't an attack, and air alone can't
    # capture (per the turn-order rule), so an undefended foreign
    # territory isn't a legal air combat-move destination either.
    # Deliberately not the plain `contested` flag on its own (see
    # _classify_combat_hop's matching fix, same session): a friendly
    # Mechanized Infantry blitzing through tid earlier this same batch
    # also sets contested_by even though nobody defended it, and that's
    # not a real attack target for an air unit either -- only actual
    # physical presence is.
    def is_attack_target(tid):
        return (_enemies_present(tid, owner, game_state, territories, unit_defs)
                or _enemy_transports_present(tid, owner, game_state, territories, unit_defs))
    return {tid for tid in reachable if is_attack_target(tid)}


def find_emergency_landing(origin_id, owner, game_state, data_module, rng):
    """Where a defending aircraft whose carrier was just destroyed can put
    down. A ONE-HOP adjacent-territory search -- not an ordinary move, and
    not gated by the aircraft's own move budget. This is a pure spatial
    query; WHEN and for WHOM it's appropriate to call it is entirely
    engine.py's job (not yet built) to enforce, per rules.json's
    emergency_landing rule:
    - only resolved once the whole battle has concluded, never mid-battle
      -- an aircraft whose carrier dies in round 1 of a 3-round battle
      keeps fighting normally through any later rounds, exactly as if
      nothing had happened, and this search only runs afterward, for
      aircraft still standing in a zone that ends the battle with no own
      carrier left;
    - only for DEFENDING aircraft -- an attacker's aircraft that loses its
      carrier has no equivalent rescue.

    Priority among adjacent territories: an own carrier first, then own
    land, then allied land -- notably NOT an ally's carrier (unlike
    legal_air_move_destinations' noncombat landing rule, where an ally's
    carrier zone is fine to voluntarily fly into since the mover might
    park their own carrier there later the same turn; this is a forced,
    no-choice emergency landing, so it's held to the stricter "must
    actually be a safe home" standard). Random choice among ties within
    the same tier -- takes an explicit rng for determinism, same
    convention as combat.py's resolve_battle. Returns the chosen
    territory id, or None if nothing qualifies -- the aircraft is lost.
    """
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    unit_defs = data_module.units()

    own_carrier, own_land, allied_land = [], [], []
    for neighbor_id in adjacency.get(origin_id, []):
        if _is_neutral(neighbor_id, game_state):
            continue
        dest = game_state.territories[neighbor_id]
        if territories[neighbor_id]['type'] == 'land':
            if dest.owner == owner:
                own_land.append(neighbor_id)
            elif _is_ally_or_self(game_state, owner, dest.owner):
                allied_land.append(neighbor_id)
        elif any(u.owner == owner and abilities.has(unit_defs, u.unit_type, abilities.CARRIER_AIR_WING) for u in dest.units):
            own_carrier.append(neighbor_id)

    for tier in (own_carrier, own_land, allied_land):
        if tier:
            return rng.choice(tier)
    return None
