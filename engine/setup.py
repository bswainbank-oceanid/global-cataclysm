"""
Builds an initial GameState from a scenario file plus a per-faction power-
mode assignment. HUMAN/BOT factions get the scenario's purchases/
promotions/carrier_escorts/naval_deploy_overrides placed on the board
directly (this is initial deployment, not a pending purchase -- units are
live from turn 0, and the design doc's 200/100 IPC starting budget never
touches a faction's treasury; it's spent once, here). DEFENSIVE factions
always use the checked-in 100-IPC/no-SC scenario for their own units,
regardless of which scenario the rest of the game uses -- that's the
ruleset the design doc specifies for a non-turn-taking defender, and it's
already generated and validated, so this reuses it rather than
re-deriving it (tools/generate_scenario*.py are scripts with top-level
side effects, not safe to import -- see engine/data.py's docstring).
NEUTRAL factions get zero units; their territories stay "owned" by that
faction code (for identity/color) but it's the faction's mode, not a
per-territory flag, that makes the territory impassable -- see
engine/movement.py.
"""
from . import data
from .state import GameState, TerritoryState, FactionState, UnitInstance, PowerMode, Phase


def _sea_zone_name_to_id():
    return {t['name']: tid for tid, t in data.territories().items() if t['type'] == 'sea'}


def _resolve_naval_zone(faction, buy_at_territory_id, unit_type, overrides, name_to_id):
    fac_overrides = overrides.get(faction, {})
    zone_name = fac_overrides.get(str(buy_at_territory_id), {}).get(unit_type)
    if zone_name:
        return name_to_id[zone_name]
    return data.default_sea_zone(buy_at_territory_id)


def _place_faction_units(gs, faction, scenario, name_to_id):
    """Places every unit in `scenario`'s purchases/carrier_escorts for
    `faction` onto gs.territories. Returns {(buy_at_territory_id,
    unit_type): [UnitInstance, ...]} in placement order, keyed by where
    the unit was BOUGHT (a land territory, even for naval units/carrier
    escorts that deploy to a sea zone) -- that's how promotions.json's
    entries are keyed, so _apply_promotions needs the same key."""
    unit_defs = data.units()
    bought_at = {}

    # How many more of a given air unit type purchased at a territory
    # still need to be redirected to a carrier's sea zone as an escort,
    # rather than deployed on land -- carrier_escorts entries reference
    # the land territory both the carrier and its escort were bought at.
    escort_remaining = {}
    for esc in scenario.get('carrier_escorts', {}).get(faction, []):
        key = (esc['aircraft_tid'], esc['unit'])
        escort_remaining[key] = escort_remaining.get(key, 0) + esc['qty']

    overrides = scenario.get('naval_deploy_overrides', {})
    for entry in scenario['purchases'].get(faction, []):
        buy_at = entry['territory_id']
        for u in entry['units']:
            unit_type, qty = u['unit'], u['qty']
            category = unit_defs[unit_type]['category']
            for _ in range(qty):
                instance = UnitInstance(
                    unit_id=gs.new_unit_id(),
                    unit_type=unit_type,
                    owner=faction,
                    current_hp=unit_defs[unit_type]['hp'],
                )
                bought_at.setdefault((buy_at, unit_type), []).append(instance)

                dest = buy_at
                if category == 'Sea':
                    dest = _resolve_naval_zone(faction, buy_at, unit_type, overrides, name_to_id)
                elif category == 'Air':
                    key = (buy_at, unit_type)
                    if escort_remaining.get(key, 0) > 0:
                        escort_remaining[key] -= 1
                        dest = _resolve_naval_zone(faction, buy_at, 'Aircraft Carrier', overrides, name_to_id)
                gs.territories[dest].units.append(instance)
    return bought_at


def _apply_promotions(scenario, faction, bought_at):
    for p in scenario.get('promotions', {}).get(faction, []):
        for inst in bought_at.get((p['territory_id'], p['unit']), []):
            if not inst.promoted:
                inst.promoted = True
                inst.current_hp += 1  # promotion grants +1 max HP; heal it in immediately
                break


def build_game_state(scenario_name, power_modes, defensive_scenario_name='starting_setup_100ipc'):
    """scenario_name: e.g. 'starting_setup_200ipc', used for every HUMAN/
    BOT faction. power_modes: {faction_code: PowerMode}, one entry per
    faction in data.factions(). Returns a fresh GameState at global_turn 0
    with every territory's TerritoryState created (land territories'
    owner comes from territories.json; sea territories have no owner),
    populated with starting units for HUMAN/BOT/DEFENSIVE factions, and
    active_faction set to the first HUMAN/BOT faction in turn order."""
    gs = GameState(global_turn=0, phase=Phase.PURCHASE)

    for tid, t in data.territories().items():
        owner = t.get('faction') if t['type'] == 'land' else None
        gs.territories[tid] = TerritoryState(territory_id=tid, owner=owner)

    for code in data.factions():
        gs.factions[code] = FactionState(code=code, mode=power_modes[code], treasury_ipc=0)

    main_scenario = data.scenario(scenario_name)
    defensive_scenario = None
    name_to_id = _sea_zone_name_to_id()

    for code, fstate in gs.factions.items():
        if fstate.mode == PowerMode.NEUTRAL:
            continue
        if fstate.mode == PowerMode.DEFENSIVE:
            if defensive_scenario is None:
                defensive_scenario = data.scenario(defensive_scenario_name)
            bought_at = _place_faction_units(gs, code, defensive_scenario, name_to_id)
            _apply_promotions(defensive_scenario, code, bought_at)
        else:  # HUMAN or BOT
            bought_at = _place_faction_units(gs, code, main_scenario, name_to_id)
            _apply_promotions(main_scenario, code, bought_at)

    active = gs.active_powers()
    gs.active_faction = active[0] if active else None
    return gs
