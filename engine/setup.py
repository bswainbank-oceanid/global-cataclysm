"""
Builds an initial GameState from a scenario file plus a per-faction faction-
mode assignment. HUMAN/BOT factions get the scenario's purchases/
promotions/carrier_escorts/naval_deploy_overrides placed on the board
directly (this is initial deployment, not a pending purchase -- units are
live from turn 0, and the design doc's 200/100 MPC starting budget never
touches a faction's treasury_mpc; it's a separate, one-time bootstrapping
budget spent once, here, on units directly). DEFENSIVE factions always
use the checked-in 100-IPC/no-SC scenario file for their own units,
regardless of which scenario the rest of the game uses -- that's the
ruleset the design doc specifies for a non-turn-taking defender, and it's
already generated and validated, so this reuses it rather than
re-deriving it (tools/generate_scenario*.py are scripts with top-level
side effects, not safe to import -- see engine/data.py's docstring).
NEUTRAL factions get zero units; their territories stay "owned" by that
faction code (for identity/color) but it's the faction's mode, not a
per-territory flag, that makes the territory impassable -- see
engine/movement.py. Every faction's treasury_mpc (including DEFENSIVE/
NEUTRAL's, though they never spend it) is then seeded with its starting
MPC income (engine/economy.py's compute_income, see data/rules.json's
production.income_formula) once its territories are in place --
ordinarily 31 MPC (25 base territory value + 3 Strategic Centers x 2).
"""
import random

from . import data
from .bots.alliance_policy import resolve_alliance_behavior, resolve_alliance_strategy
from .economy import compute_income
from .state import GameState, TerritoryState, FactionState, UnitInstance, FactionMode, Phase


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


def build_game_state(scenario_name, faction_modes, defensive_scenario_name='starting_setup_100ipc',
                      randomize_play_order=True, allow_combat_moves_first_turn=False,
                      allow_noncombat_moves_first_turn=True, max_alliance_size=2,
                      can_withdraw_from_alliances=True, can_rejoin_alliances=False,
                      alliance_strategies=None, alliance_behaviors=None, rng=None):
    """scenario_name: e.g. 'starting_setup_200ipc', used for every HUMAN/
    BOT faction. faction_modes: {faction_code: FactionMode}, one entry per
    faction in data.factions(). Returns a fresh GameState at global_turn 0
    with every territory's TerritoryState created (land territories'
    owner comes from territories.json; sea territories have no owner),
    populated with starting units for HUMAN/BOT/DEFENSIVE factions, and
    active_faction set to the first HUMAN/BOT faction in turn order.

    game_start_settings, chosen once here at game creation:
    - randomize_play_order (default True): shuffles the order factions
      are inserted into GameState.factions -- GameState.active_factions()
      cycles through them in that same order (dict insertion order),
      and it's also what combat.contested_territory_rule's capture-claim
      tie-break uses as "turn order" -- so this one shuffle drives both
      consistently. Takes an explicit `rng` (a random.Random instance)
      for deterministic tests/replays; defaults to a fresh, unseeded one.
    - allow_combat_moves_first_turn (default False) / allow_noncombat_
      moves_first_turn (default True): stored directly on GameState and
      enforced every turn by GameEngine.advance_phase(), not just at
      setup -- see GameState's own docstring comment on these fields.
    - max_alliance_size (default 2, 1-5) / can_withdraw_from_alliances
      (default True) / can_rejoin_alliances (default False): stored
      directly on GameState and enforced every Alliances phase by
      GameEngine.invite_to_alliance/withdraw_from_alliance -- not
      validated here (a value outside 1-5 is trusted, not rejected).

    alliance_strategies / alliance_behaviors: optional {faction_code: str}
    -- per-BOT game-start settings (engine.bots.alliance_policy), each
    value one of that module's STRATEGIES/BEHAVIORS, 'random', or simply
    omitted (also treated as 'random'). Resolved ONCE here via
    resolve_alliance_strategy/resolve_alliance_behavior (using `rng`, the
    same one randomize_play_order uses) and stored as the concrete result
    on FactionState.alliance_strategy/alliance_behavior -- fixed for the
    rest of the game even when the input was 'random'. Only ever set for
    FactionMode.BOT factions; HUMAN/DEFENSIVE/NEUTRAL factions never
    consult this policy layer, so their fields stay None."""
    rng = rng or random.Random()
    gs = GameState(
        global_turn=0, phase=Phase.PURCHASE,
        allow_combat_moves_first_turn=allow_combat_moves_first_turn,
        allow_noncombat_moves_first_turn=allow_noncombat_moves_first_turn,
        max_alliance_size=max_alliance_size,
        can_withdraw_from_alliances=can_withdraw_from_alliances,
        can_rejoin_alliances=can_rejoin_alliances,
    )

    for tid, t in data.territories().items():
        owner = t.get('faction') if t['type'] == 'land' else None
        gs.territories[tid] = TerritoryState(territory_id=tid, owner=owner)

    faction_codes = list(data.factions())
    if randomize_play_order:
        rng.shuffle(faction_codes)
    for code in faction_codes:
        gs.factions[code] = FactionState(code=code, mode=faction_modes[code], treasury_mpc=0)

    alliance_strategies = alliance_strategies or {}
    alliance_behaviors = alliance_behaviors or {}
    for code, fstate in gs.factions.items():
        if fstate.mode != FactionMode.BOT:
            continue
        fstate.alliance_strategy = resolve_alliance_strategy(alliance_strategies.get(code), rng)
        fstate.alliance_behavior = resolve_alliance_behavior(alliance_behaviors.get(code), rng)

    for code, fstate in gs.factions.items():
        # Starting treasury: this faction's territories are already
        # placed above, so its opening MPC income (per
        # production.income_formula) is computable now -- e.g. 25 base
        # territory value + 3 Strategic Centers x 2 = 31 MPC for a
        # standard faction. This is separate from, and unrelated to, the
        # one-time 200/100-MPC scenario budget spent above to buy each
        # faction's STARTING UNITS -- that's a bootstrapping budget, not
        # treasury_mpc.
        fstate.treasury_mpc = compute_income(code, gs, data)

    main_scenario = data.scenario(scenario_name)
    defensive_scenario = None
    name_to_id = _sea_zone_name_to_id()

    for code, fstate in gs.factions.items():
        if fstate.mode == FactionMode.NEUTRAL:
            continue
        if fstate.mode == FactionMode.DEFENSIVE:
            if defensive_scenario is None:
                defensive_scenario = data.scenario(defensive_scenario_name)
            bought_at = _place_faction_units(gs, code, defensive_scenario, name_to_id)
            _apply_promotions(defensive_scenario, code, bought_at)
        else:  # HUMAN or BOT
            bought_at = _place_faction_units(gs, code, main_scenario, name_to_id)
            _apply_promotions(main_scenario, code, bought_at)

    active = gs.active_factions()
    gs.active_faction = active[0] if active else None
    return gs
