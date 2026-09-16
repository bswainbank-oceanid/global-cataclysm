"""
Legal-move computation: given a unit's current position, what territories/
sea zones can it legally reach this turn? Answers reachability queries
only -- it does not execute a move or apply consequences (capture,
becoming Transport cargo, the sea-battle-before-land-battle dependency
for an amphibious landing), which is engine.py's job once built. Every
rule here matches data/rules.json's movement section exactly; see that
file for the prose version of each rule this implements.

Two move types, with different destination rules:
- combat: the destination becomes an attack (or joins one already in
  progress). Ends when the path enters foreign or contested territory --
  Mechanized Infantry is the sole exception, able to pass through (and
  capture) an EMPTY foreign land territory and keep going.
- noncombat: reposition only. Friendly or already-contested-by-you
  territory; never a clean foreign one.

Movement budget: a unit's move stat, extended by +1 (to both move types,
for the rest of the turn) the moment it's in a sea zone -- whether it
started there or entered one mid-move. This is applied dynamically as
the search proceeds, not precomputed, since a unit's remaining budget can
change partway through its own move.

There's no such thing as a Transport moving under its own query here --
Transport isn't purchasable (units.json) and has no independent
existence: it comes into being automatically when a land unit enters
water and disappears when that unit returns to land. It's purely a
combat-participation wrapper (see engine/combat.py, where it IS a real
target/attacker in a sea battle) -- movement.py's land-unit water-
crossing bonus above is the complete model for what happens when a land
unit is "in a Transport"; nothing here ever calls legal_*_move_
destinations with unit_type='Transport'.
"""
from .state import PowerMode


def _base_move(unit_type, move_type, unit_defs):
    stats = unit_defs[unit_type]
    key = 'combat_move' if move_type == 'combat' else 'non_combat_move'
    return stats[key]


def _is_neutral(territory_id, game_state):
    owner = game_state.territories[territory_id].owner
    return owner is not None and game_state.factions[owner].mode == PowerMode.NEUTRAL


def _is_contested(territory_id, game_state):
    return bool(game_state.territories[territory_id].contested_by)


def _enemies_present(territory_id, mover_faction, game_state):
    """Enemy combatants in a territory -- Transports never count as an
    occupying presence, per enemy_occupation_stop_rule."""
    return any(u.owner != mover_faction and u.unit_type != 'Transport'
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


def _classify_combat_hop(dest_id, mover_faction, unit_type, is_land_unit, game_state, territories):
    if _is_neutral(dest_id, game_state):
        return BLOCKED

    dest = game_state.territories[dest_id]
    is_land = territories[dest_id]['type'] == 'land'
    contested = _is_contested(dest_id, game_state)

    if not is_land and is_land_unit and (contested or _enemies_present(dest_id, mover_faction, game_state)):
        # Hostile (occupied OR contested) sea zone, land unit currently
        # in transit (i.e. riding a Transport): must be prepared to
        # fight/rejoin the naval battle here, but may continue straight
        # on to adjacent LAND in the same move -- to attack, join an
        # ongoing fight, or land safely on friendly territory to escape
        # the hostile water. Landing there is always a legal stop for
        # this move regardless of whose land it is (see the BFS driver,
        # which enforces that even though the ordinary classification of
        # friendly land below wouldn't otherwise mark it as one) --
        # still a combat move, not a non-combat one, since it passed
        # through contested/occupied water to get there. Actually
        # completing the crossing is contingent on surviving that sea
        # zone's naval battle first (see combat.battle_resolution_pass_order),
        # an execution-time dependency, not a reachability concern.
        return STOP_AND_PASS_LAND_ONLY

    if is_land and dest.owner == mover_faction and not contested:
        return PASS_ONLY  # own territory, uncontested -- just transit; attacking your own land isn't a thing

    if contested:
        # Joining or continuing a fight already in progress -- a legal
        # place to end a combat move, but combat move must result in an
        # attack, not walk past one; never a pass-through.
        return STOP_ONLY

    if is_land and dest.owner == mover_faction:
        # own territory, contested -- freely transitable, same as uncontested own land
        return PASS_ONLY

    if _enemies_present(dest_id, mover_faction, game_state):
        return STOP_ONLY  # occupied foreign territory (or occupied sea for a non-land unit): attack, stop here

    # Empty (no defenders, not contested). For sea zones this is just
    # ordinary open water -- always pass-through, no ownership/capture
    # concept applies to sea. For land, it's a foreign territory nobody
    # is defending: capturable, and only Mechanized Infantry may
    # continue past it in the same move.
    if not is_land:
        return PASS_ONLY
    if unit_type == 'Mechanized Infantry':
        return STOP_AND_PASS
    return STOP_ONLY


def _classify_noncombat_hop(dest_id, mover_faction, game_state, territories):
    """Noncombat move: friendly or contested-by-you territory only, pass
    or stop identically (no attack semantics, no Mech Inf exception --
    that's a combat-move-only ability)."""
    if _is_neutral(dest_id, game_state):
        return BLOCKED
    dest = game_state.territories[dest_id]
    is_land = territories[dest_id]['type'] == 'land'
    if is_land and dest.owner != mover_faction and not _is_contested(dest_id, game_state):
        return BLOCKED  # never a clean foreign territory
    return STOP_AND_PASS


def _reachable_destinations(origin_id, mover_faction, unit_type, move_type, game_state, data_module):
    """Core BFS shared by combat/noncombat reachability. Tracks, per
    territory, the best (largest) remaining-budget-on-arrival seen so
    far, and only explores a neighbor when arriving with a strictly
    better remaining budget than any prior visit -- since budget only
    decreases per hop (aside from the one-time water bonus), this
    naturally terminates without needing a separate depth cap. (Known
    simplification: this pruning is budget-only, not budget-plus-
    land_only-restriction -- a node reached both via an unrestricted
    path and, with a better budget, via a land-only-restricted one could
    in principle have the unrestricted path's further options pruned
    away. Narrow edge case, and fails conservatively -- toward missing a
    legal destination, never toward allowing an illegal one.)"""
    unit_defs = data_module.units()
    territories = data_module.territories()
    adjacency = data_module.adjacency()
    is_land_unit = unit_defs[unit_type]['category'] == 'Land'

    base_budget = _base_move(unit_type, move_type, unit_defs)
    # The water bonus is a LAND-unit-becomes-Transport-cargo concept --
    # it never applies to a naturally sea-based unit (already home in
    # water, nothing to bonus) or, via legal_air_move_destinations, an
    # air unit (handled entirely separately, no water concept at all).
    started_in_water = is_land_unit and territories[origin_id]['type'] == 'sea'
    initial_budget = base_budget + (1 if started_in_water else 0)

    destinations = set()
    best_seen = {origin_id: initial_budget}
    # stack entries: (territory_id, moves_used_so_far, water_bonus_active, land_only_restricted)
    stack = [(origin_id, 0, started_in_water, False)]

    while stack:
        current_id, moves_used, water_active, land_only = stack.pop()
        for neighbor_id in adjacency.get(current_id, []):
            neighbor_is_land = territories[neighbor_id]['type'] == 'land'
            if land_only and not neighbor_is_land:
                continue  # amphibious continuation past occupied water: land only, never further open sea
            new_water_active = water_active or (is_land_unit and not neighbor_is_land)
            budget = base_budget + (1 if new_water_active else 0)
            new_moves_used = moves_used + 1
            if new_moves_used > budget:
                continue
            remaining = budget - new_moves_used
            if remaining <= best_seen.get(neighbor_id, -1):
                continue  # already reached this territory with an equal-or-better remaining budget
            best_seen[neighbor_id] = remaining

            if move_type == 'combat':
                hop = _classify_combat_hop(neighbor_id, mover_faction, unit_type, is_land_unit, game_state, territories)
            else:
                hop = _classify_noncombat_hop(neighbor_id, mover_faction, game_state, territories)
            # Landing on this neighbor via the hostile-water escape/
            # amphibious exception is always a legal stop, even when the
            # neighbor's own ordinary classification wouldn't otherwise
            # mark it as one (e.g. friendly uncontested land, which is
            # normally pass-through-only) -- it's still a combat move,
            # just one that ends in a safe landing rather than an attack.
            if hop.stop or (land_only and neighbor_is_land):
                destinations.add(neighbor_id)
            if hop.pass_through:
                stack.append((neighbor_id, new_moves_used, new_water_active, hop.pass_through == 'land_only'))

    return destinations


def legal_combat_move_destinations(unit_type, owner, origin_id, game_state, data_module):
    """Territories/sea zones `owner`'s `unit_type` unit, currently at
    `origin_id`, could legally end a combat move at. Air units should
    use legal_air_move_destinations instead (they ignore occupation
    entirely and aren't subject to the land/sea pass-through rules
    here)."""
    return _reachable_destinations(origin_id, owner, unit_type, 'combat', game_state, data_module)


def legal_noncombat_move_destinations(unit_type, owner, origin_id, game_state, data_module):
    """Territories/sea zones `owner`'s `unit_type` unit, currently at
    `origin_id`, could legally end a non-combat move at."""
    return _reachable_destinations(origin_id, owner, unit_type, 'noncombat', game_state, data_module)


def legal_air_move_destinations(unit_type, owner, origin_id, move_type, game_state, data_module):
    """Air units fly over everyone freely (never blocked, never forced
    to stop by occupation) in both move phases -- the only constraint is
    their own move budget (no water bonus; that's a land-unit/Transport
    concept) and where they're allowed to end the turn: a combat move
    still needs to land in a legal spot (friendly territory, or an
    enemy/contested space to attack), while a non-combat move is
    restricted to friendly territory or a carrier -- never a contested
    LAND territory even though a contested sea zone's carrier is fine."""
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
            if _is_neutral(neighbor_id, game_state):
                continue
            reachable.add(neighbor_id)
            stack.append((neighbor_id, new_moves_used))

    if move_type == 'noncombat':
        def legal_landing(tid):
            dest = game_state.territories[tid]
            if territories[tid]['type'] == 'land':
                return dest.owner == owner and not dest.contested_by  # friendly AND uncontested land only
            has_own_carrier = any(u.owner == owner and u.unit_type == 'Aircraft Carrier' for u in dest.units)
            return has_own_carrier
        return {tid for tid in reachable if legal_landing(tid)}

    return reachable
