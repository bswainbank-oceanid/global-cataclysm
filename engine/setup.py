"""
Builds an initial GameState from the scenario's starting setups (InitialSetup and
UnitPromotions modules, see docs/DATA_MODEL.md) plus a per-faction faction-mode
assignment. HUMAN/BOT factions get their units from the scenario's standard setup,
placed on the board directly (initial deployment, not a pending purchase -- units are
live from turn 0, and the setup's MPC budget never touches a faction's treasury_mpc).
DEFENSIVE factions always take theirs from the scenario's defensive setup -- the
smaller, no-Strategic-Center ruleset the design doc specifies for a non-turn-taking
defender. NEUTRAL factions get zero units; their territories stay "owned" by that
faction code (for identity/color) but it's the faction's mode, not a per-territory
flag, that makes the territory impassable -- see engine/movement.py. Every faction's
treasury_mpc (including DEFENSIVE/NEUTRAL's, though they never spend it) is then
seeded with its starting MPC income (engine/economy.py's compute_income, see the
rule set's production.income_formula) once its territories are in place --
ordinarily 31 MPC (25 base territory value + 3 Strategic Centers x 2).
"""
import random
import re

from . import data
from .bots.alliance_policy import (
    reroll_alliance_behavior, reroll_alliance_strategy, resolve_alliance_behavior, resolve_alliance_strategy,
)
from .economy import compute_income
from .state import GameState, TerritoryState, FactionState, UnitInstance, FactionMode, Phase


def _natural_key(unit_id):
    """Sort key putting 'NAA-2' before 'NAA-10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', str(unit_id))]


def _place_faction_units(gs, faction, setup, promotions, unit_defs):
    """Places `faction`'s units from an InitialSetup module onto gs.territories, in unit id
    order (which is what fixes each unit's unit_id), then applies its starting promotions
    (each one also heals in its +1 max HP). An air unit placed in a sea zone starts on its
    faction's carrier there."""
    rows = [(u['id'], u['unit_type_id'], loc['location_id'])
            for loc in setup['locations'] for u in loc['units'] if u['faction_id'] == faction]
    by_setup_id = {}
    for setup_id, unit_type, location_id in sorted(rows, key=lambda r: _natural_key(r[0])):
        instance = UnitInstance(
            unit_id=gs.new_unit_id(),
            unit_type=unit_type,
            owner=faction,
            current_hp=unit_defs[unit_type]['hp'],
        )
        by_setup_id[setup_id] = instance
        gs.territories[location_id].units.append(instance)
    for p in promotions.get('units', []):
        instance = by_setup_id.get(p['unit_id'])
        if instance is not None:
            instance.promotions += p['num_promotions']
            instance.current_hp += p['num_promotions']


def _check_starting_alliances(groups, faction_modes, max_alliance_size):
    """Raises ValueError for a starting-alliance list the game could not start
    from: a group needs at least two members, every member must be an active
    faction (HUMAN or BOT), no faction may be in two groups, no group may exceed
    max_alliance_size, and no group may contain EVERY active faction (that alliance
    would be the game ending -- see GameEngine.would_game_end)."""
    if not groups:
        return
    active = {c for c, m in faction_modes.items() if m in (FactionMode.HUMAN, FactionMode.BOT)}
    seen = set()
    for members in groups:
        members = list(members)
        if len(members) < 2:
            raise ValueError(f'a starting alliance needs at least two members, got {members}')
        for code in members:
            if code not in active:
                raise ValueError(f'{code} cannot be in a starting alliance: it is not a HUMAN or BOT faction')
            if code in seen:
                raise ValueError(f'{code} is in more than one starting alliance')
            seen.add(code)
        if len(members) > max_alliance_size:
            raise ValueError(f'starting alliance {members} is larger than max_alliance_size {max_alliance_size}')
        if set(members) >= active:
            raise ValueError('a starting alliance of every active faction would end the game at once')


def build_game_state(faction_modes, randomize_play_order=True, allow_combat_moves_first_turn=False,
                      allow_noncombat_moves_first_turn=True, max_alliance_size=2,
                      can_withdraw_from_alliances=True, can_rejoin_alliances=False,
                      alliance_strategies=None, alliance_behaviors=None, rng=None,
                      starting_alliances=None):
    """faction_modes: {faction_code: FactionMode}, one entry per
    faction in data.factions(). Returns a fresh GameState at global_turn 0
    with every territory's TerritoryState created (land territories'
    owner comes from the scenario's faction assignment; sea territories have no owner),
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
    - max_alliance_size (default 2) / can_withdraw_from_alliances
      (default True) / can_rejoin_alliances (default False): stored
      directly on GameState and enforced every Diplomacy phase by
      GameEngine.invite_to_alliance/withdraw_from_alliance -- not
      validated here (trusted, not range-checked against the number of
      factions in play). max_alliance_size is only ever a CEILING:
      GameEngine._effective_max_alliance_size() further caps it to
      (active faction count - 1), recomputed fresh on every invite as
      factions are eliminated over the game, not just once here at
      setup -- so passing something like 5 in a 3-faction game is
      harmless, not an error; the effective cap starts at 2 either way.

    starting_alliances: optional list of lists of faction codes; each inner list
    starts the game already allied (one shared alliance tag, as if formed by
    invitations). See _check_starting_alliances for what is rejected.

    alliance_strategies / alliance_behaviors: optional {faction_code: str}
    -- per-BOT game-start settings (engine.bots.alliance_policy), each
    value one of that module's STRATEGIES/BEHAVIORS (now including
    'variable'), 'random', or simply omitted (also treated as 'random').
    Resolved ONCE here via resolve_alliance_strategy/resolve_alliance_
    behavior (using `rng`, the same one randomize_play_order uses) and
    stored as the concrete result on FactionState.alliance_strategy/
    alliance_behavior -- fixed for the rest of the game even when the
    input was 'random' (landing on 'variable' is exactly as permanent a
    result as landing on any other single value would have been). A
    'variable' result additionally gets an initial concrete re-roll right
    away too (FactionState.current_alliance_strategy/current_alliance_
    behavior, via reroll_alliance_strategy/reroll_alliance_behavior) so
    there's a real decision from turn 1, not None -- see RandomBot.
    _maybe_reroll_variable_alliance_settings for the same re-roll
    repeating every turn after that. Only ever set for FactionMode.BOT
    factions; HUMAN/DEFENSIVE/NEUTRAL factions never consult this policy
    layer, so their fields stay None."""
    rng = rng or random.Random()
    _check_starting_alliances(starting_alliances, faction_modes, max_alliance_size)
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
        # A Defensive power has no Strategic Centers, and its territory keeps that status
        # for good -- even once someone else captures it.
        defensive_home = owner is not None and faction_modes.get(owner) == FactionMode.DEFENSIVE
        gs.territories[tid] = TerritoryState(territory_id=tid, owner=owner, sc_disabled=defensive_home)

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
        # 'variable' gets its first concrete pick right away too, so
        # there's a real decision from turn 1 (current_alliance_strategy/
        # current_alliance_behavior would otherwise start out None until
        # this bot's own first Purchase phase re-rolls them -- see
        # RandomBot._maybe_reroll_variable_alliance_settings).
        if fstate.alliance_strategy == 'variable':
            fstate.current_alliance_strategy = reroll_alliance_strategy(rng)
        if fstate.alliance_behavior == 'variable':
            fstate.current_alliance_behavior = reroll_alliance_behavior(rng)

    for code, fstate in gs.factions.items():
        # Starting treasury: this faction's territories are already
        # placed above, so its opening MPC income (per
        # production.income_formula) is computable now -- e.g. 25 base
        # territory value + 3 Strategic Centers x 2 = 31 MPC for a
        # standard faction. This is separate from, and unrelated to, the
        # one-time 125/100-MPC scenario budget spent above to buy each
        # faction's STARTING UNITS -- that's a bootstrapping budget, not
        # treasury_mpc.
        fstate.treasury_mpc = compute_income(code, gs, data)

    unit_defs = data.units()
    setups = {}
    for code, fstate in gs.factions.items():
        if fstate.mode == FactionMode.NEUTRAL:
            continue
        kind = 'defensive' if fstate.mode == FactionMode.DEFENSIVE else 'standard'  # HUMAN or BOT: standard
        if kind not in setups:
            setups[kind] = data.initial_setup(kind)
        setup, promotions = setups[kind]
        _place_faction_units(gs, code, setup, promotions, unit_defs)

    for members in starting_alliances or []:
        tag = f'ALLIANCE_{gs._next_alliance_id}'  # same tags GameEngine._new_alliance_tag hands out mid-game
        gs._next_alliance_id += 1
        for code in members:
            gs.factions[code].alliance = tag

    active = gs.active_factions()
    gs.active_faction = active[0] if active else None
    return gs
