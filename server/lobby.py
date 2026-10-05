"""
The game launch screen's server side: validate a set of launch settings and build
the game they describe.

Settings (the "new_game" message's "settings"):

    {"scenario": {"kind": "fixed" | "new",               # default fixed: the GC72 scenario as it stands
                  "options": {...},                      # new: the generator's scenario-wide options (see generator_info); omitted ones take its defaults
                  "neutral": {"territory_value", "units_mpc", "promotions", "scs"}},   # new: the Neutral row (omitted: defaults)
     "seats": [ {"mode": "HUMAN" | "BOT" | "NEUTRAL" | "NONCOMBATANT",   # a fixed scenario
                        | "HUMAN" | "BOT" | "NOT_PLAYING",               # a new one
                 "faction": "random" | "NAA" | "UE" | "UER" | "GPC" | "PAF" | "AAC",
                 "alliance": 0 | 1 | 2 | 3,             # 0 = none; players only
                 "strategy": "random" | aggressive | passive | counterweight | independent | variable,   # bots only
                 "ai": "strategy" | "random" | "claude",                                                  # bots only: the heuristic bot (default), the random baseline, or Claude itself
                 "behavior": "random" | loyal | opportunistic | treacherous | variable,                   # bots only
                 "territory_value", "initial_mpc", "units_mpc", "promotions", "scs"},                      # a new scenario's players (omitted: defaults)
                ... one per faction (six) ...],
     "randomize_order": true,                            # default true
     "can_withdraw": true,                               # players may leave an alliance (default true)
     "can_rejoin": false,                                # ...and may re-ally with those they left (default false)
     "max_alliance_size": 3,                             # most factions in one alliance (default 3; 1 .. players-1; 1 = no alliances and no Alliances phase)
     "allow_combat_first_turn": false,                   # may a faction make Combat Moves on its first turn (default false)
     "allow_noncombat_first_turn": true,                 # ...and Non-Combat Moves (default true)
     "armistice_rounds": 10,                             # rounds until the game proposes an armistice (0-100, default 10; 0 = never)
     "seed": 12345,                                      # optional: a seeded game replays exactly (setup, bots AND dice)
     "dev": {"combat_first_turn": false}}                # optional, testing only

Every faction is in exactly one seat: explicit picks are honoured first (and must
be distinct), "random" seats then take the factions left over, in random order.
"Players" are the HUMAN and BOT seats; NEUTRAL seats' units defend but take no
turns, and NONCOMBATANT seats are impassable (see the rule set power_modes).

A "new" scenario is dealt by engine/scenario_generator.py from the game's seed: only the
players' factions take part (a NOT_PLAYING seat's faction is left out of the game
altogether), the generator's Neutral pool and Noncombatant land take the rest.

The rules the screen enforces (and the server re-checks, being the authority):
at least two players, at most one human, distinct explicit factions, and each
starting alliance (seats sharing an alliance number) has two or more members and
does not include every player, and fits within max_alliance_size. Bots' strategy/behavior "random" is resolved once
by the engine at game start. All problems are reported together in
LobbyError.problems.
"""
import random

from engine import data as data_module
from engine import scenario_generator
from engine.game_config import GameConfig
from engine.repository import default_repository
from engine.bots.alliance_policy import BEHAVIORS, STRATEGIES
from engine.bots.claude_bot import ClaudeBot
from engine.bots.random_bot import RandomBot
from engine.bots.planner import DEFAULT_BUDGET
from engine.bots.strategy_bot import StrategyBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode
from engine.stats import GameStats
from engine.turn_log import TurnLog
from .session import GameSession


def seat_count():
    """One seat per faction in the scenario (six in GC72)."""
    return len(data_module.factions())

MODES = ('HUMAN', 'BOT', 'NEUTRAL', 'NONCOMBATANT')
NEW_SCENARIO_MODES = ('HUMAN', 'BOT', 'NOT_PLAYING')
SCENARIO_KINDS = ('fixed', 'new')
GENERATOR_ID = 'GC72_Generator'


def generator():
    """The ScenarioGenerator module the launcher's New Scenario uses."""
    return default_repository().get('ScenarioGenerator', GENERATOR_ID)


def base_config():
    return GameConfig(generator()['base_scenario_id'])


def generator_info():
    """What the launch screen needs to show New Scenario: the scenario-wide options [{key, label, kind, min,
    max, default, help}] and the seat settings {total, players: [...], neutral: [...]}."""
    g, base = generator(), base_config()
    return {'options': scenario_generator.option_specs(base, g['defaults']),
            'seats': scenario_generator.seat_specs(base, g['seat_defaults'], g['neutral_defaults'])}


def _seat_settings(seat):
    return {k: seat[k] for k in scenario_generator.SEAT_SETTINGS if k in seat}


def _scenario(settings):
    s = settings.get('scenario') or {}
    return s.get('kind', 'fixed'), s.get('options') or {}
ALLIANCE_NUMBERS = (1, 2, 3)
PLAYER_MODES = ('HUMAN', 'BOT')
# The heuristic bot (engine/bots/strategy_bot.py), the random baseline, and Claude itself
# (engine/bots/claude_bot.py -- needs ANTHROPIC_API_KEY in the server's own environment; checked at
# ClaudeBot construction time below, not here, so this list itself never depends on a key being set).
BOT_AIS = ('strategy', 'random', 'claude')
DEFAULT_BOT_AI = 'strategy'
DEFAULT_MAX_ALLIANCE_SIZE = 3
DEFAULT_ARMISTICE_ROUNDS = 10  # the game proposes an armistice once this many rounds are played (0: never)
MAX_ARMISTICE_ROUNDS = 100


class LobbyError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


def check_settings(settings, max_humans=1):
    """Every problem with `settings`, as a list of readable strings (empty = valid). `max_humans`: the most
    HUMAN seats allowed (None: any number -- a game lobby's, server/games.py)."""
    problems = []
    seats = settings.get('seats')
    if not isinstance(seats, list) or len(seats) != seat_count():
        return [f'there must be exactly {seat_count()} seats']
    factions = list(data_module.factions())
    kind, options = _scenario(settings)
    if kind not in SCENARIO_KINDS:
        return [f'scenario kind must be one of {", ".join(SCENARIO_KINDS)}']
    modes = NEW_SCENARIO_MODES if kind == 'new' else MODES
    if kind == 'new':
        if not isinstance(options, dict):
            problems.append('scenario options must be an object')
        else:
            unknown = [k for k in options if k not in scenario_generator.OPTIONS]
            if unknown:
                problems.append(f'unknown scenario options: {", ".join(unknown)}')
            base = base_config()
            problems += scenario_generator.check_options(
                base, scenario_generator.resolve_options(base, generator()['defaults'],
                                                         {k: v for k, v in options.items() if k not in unknown}))
            total = scenario_generator.land_value_total(base)
            g = generator()
            for i, seat in enumerate(seats, 1):
                if seat.get('mode') in PLAYER_MODES:
                    values = dict(g['seat_defaults'], **_seat_settings(seat))
                    problems += scenario_generator.check_seat(values, list(scenario_generator.SEAT_SETTINGS), total,
                                                              f'seat {i}')
            neutral = (settings.get('scenario') or {}).get('neutral') or {}
            values = dict(g['neutral_defaults'], **{k: neutral[k] for k in scenario_generator.NEUTRAL_SEAT_SETTINGS
                                                    if k in neutral})
            problems += scenario_generator.check_seat(values, list(scenario_generator.NEUTRAL_SEAT_SETTINGS), total,
                                                      'Neutral')

    picked = {}
    for i, seat in enumerate(seats, 1):
        if seat.get('mode') not in modes:
            problems.append(f'seat {i}: mode must be one of {", ".join(modes)}')
            continue
        f = seat.get('faction', 'random')
        if f != 'random':
            if f not in factions:
                problems.append(f'seat {i}: unknown faction {f!r}')
            elif f in picked:
                problems.append(f'seats {picked[f]} and {i} both chose {f}')
            else:
                picked[f] = i
        if seat['mode'] == 'BOT':
            if seat.get('ai', DEFAULT_BOT_AI) not in BOT_AIS:
                problems.append(f'seat {i}: bot ai must be one of {", ".join(BOT_AIS)}')
            if seat.get('strategy', 'random') not in ('random',) + tuple(STRATEGIES):
                problems.append(f'seat {i}: unknown alliance strategy {seat.get("strategy")!r}')
            if seat.get('behavior', 'random') not in ('random',) + tuple(BEHAVIORS):
                problems.append(f'seat {i}: unknown alliance behavior {seat.get("behavior")!r}')

    if 'seed' in settings and (not isinstance(settings['seed'], int) or isinstance(settings['seed'], bool)):
        problems.append('seed must be a whole number')
    for key in ('randomize_order', 'can_withdraw', 'can_rejoin', 'allow_combat_first_turn', 'allow_noncombat_first_turn'):
        if key in settings and not isinstance(settings[key], bool):
            problems.append(f'{key} must be true or false')
    rounds = settings.get('armistice_rounds', DEFAULT_ARMISTICE_ROUNDS)
    if isinstance(rounds, float) and rounds.is_integer():
        rounds = int(rounds)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 0 <= rounds <= MAX_ARMISTICE_ROUNDS:
        problems.append(f'armistice_rounds must be a whole number from 0 to {MAX_ARMISTICE_ROUNDS}')
    max_size = settings.get('max_alliance_size', DEFAULT_MAX_ALLIANCE_SIZE)
    size_ok = isinstance(max_size, int) and not isinstance(max_size, bool)
    if not size_ok:
        problems.append('max_alliance_size must be a whole number')
        max_size = DEFAULT_MAX_ALLIANCE_SIZE

    players = [i for i, s in enumerate(seats, 1) if s.get('mode') in PLAYER_MODES]
    humans = [i for i, s in enumerate(seats, 1) if s.get('mode') == 'HUMAN']
    if len(players) < 2:
        problems.append('at least two players (humans or bots) are needed')
    if max_humans is not None and len(humans) > max_humans:
        problems.append('at most one human player is supported' if max_humans == 1
                        else f'at most {max_humans} human players are supported')

    if 'max_alliance_size' in settings and size_ok:
        if max_size < 1:
            problems.append('the maximum alliance size must be at least 1 (1 means no alliances)')
        elif len(players) >= 3 and max_size > len(players) - 1:
            problems.append(f'the maximum alliance size must be between 1 and {len(players) - 1} (the number of players minus one)')
    groups = {}
    for i in players:
        n = int(seats[i - 1].get('alliance') or 0)
        if n and n not in ALLIANCE_NUMBERS:
            problems.append(f'seat {i}: alliance must be none or one of {ALLIANCE_NUMBERS}')
        elif n:
            groups.setdefault(n, []).append(i)
    for n, members in sorted(groups.items()):
        if len(members) < 2:
            problems.append(f'Alliance {n} has only one member (seat {members[0]}): an alliance needs two or more')
        elif len(members) >= len(players) and len(players) >= 2:
            problems.append(f'Alliance {n} would contain every player, which ends the game at once')
        elif len(members) > max_size:
            problems.append(f'Alliance {n} has {len(members)} members, more than the maximum alliance size ({max_size})')
    return problems


def resolve_settings(settings, rng=None, max_humans=1):
    """Validates `settings` and resolves the random faction picks. Returns
    (assignments, groups, randomize_order): assignments is one dict per seat
    ({faction, mode, strategy, behavior}, in seat order), groups the starting
    alliances as lists of faction codes. Raises LobbyError."""
    problems = check_settings(settings, max_humans)
    if problems:
        raise LobbyError(problems)
    rng = rng or random.Random()
    seats = settings['seats']
    left = [f for f in data_module.factions() if f not in {s.get('faction') for s in seats}]
    rng.shuffle(left)
    assignments = []
    for seat in seats:
        faction = seat.get('faction', 'random')
        if faction == 'random':
            faction = left.pop()
        assignments.append({
            'faction': faction, 'mode': seat['mode'],
            'strategy': seat.get('strategy', 'random'), 'behavior': seat.get('behavior', 'random'),
            'ai': seat.get('ai', DEFAULT_BOT_AI),
        })
    by_number = {}
    for seat, a in zip(seats, assignments):
        n = int(seat.get('alliance') or 0)
        if n and seat['mode'] in PLAYER_MODES:
            by_number.setdefault(n, []).append(a['faction'])
    return assignments, [g for _, g in sorted(by_number.items())], bool(settings.get('randomize_order', True))


def build_session(settings, rng=None, auto=False, max_humans=1):
    """The GameSession for `settings`, plus the resolved seat list for the client.
    Returns (session, seats); raises LobbyError. `auto`: the session runs itself until it needs a
    human (server/session.py, auto mode); `max_humans` as check_settings'."""
    rng = rng or random.Random(settings.get('seed'))
    assignments, groups, randomize = resolve_settings(settings, rng, max_humans)
    kind, options = _scenario(settings)
    config = data_module
    if kind == 'new':
        g, base = generator(), base_config()
        players = [a['faction'] for a in assignments if a['mode'] in PLAYER_MODES]
        per_faction = {a['faction']: _seat_settings(seat) for seat, a in zip(settings['seats'], assignments)
                       if a['mode'] in PLAYER_MODES}
        seats_resolved, notes = scenario_generator.resolve_seats(
            base, g['seat_defaults'], g['neutral_defaults'], players, per_faction,
            (settings.get('scenario') or {}).get('neutral'))
        resolved = scenario_generator.resolve_options(base, g['defaults'], options)
        # its own draw from the game's seed, taken only for a new scenario: fixed games deal as before
        repo, scenario_id, _ = scenario_generator.generate(base, seats_resolved, resolved, random.Random(rng.random()),
                                                           notes=notes)
        config = GameConfig(scenario_id, repo)
    modes = {a['faction']: FactionMode[a['mode']] for a in assignments if a['mode'] != 'NOT_PLAYING'}
    gs = build_game_state(
        modes, randomize_play_order=randomize, rng=rng,
        max_alliance_size=int(settings.get('max_alliance_size', DEFAULT_MAX_ALLIANCE_SIZE)),
        alliance_strategies={a['faction']: a['strategy'] for a in assignments if a['mode'] == 'BOT'},
        alliance_behaviors={a['faction']: a['behavior'] for a in assignments if a['mode'] == 'BOT'},
        starting_alliances=groups,
        can_withdraw_from_alliances=bool(settings.get('can_withdraw', True)),
        can_rejoin_alliances=bool(settings.get('can_rejoin', False)),
        allow_combat_moves_first_turn=bool(settings.get('allow_combat_first_turn', False) or settings.get('dev', {}).get('combat_first_turn', False)),
        allow_noncombat_moves_first_turn=bool(settings.get('allow_noncombat_first_turn', True)),
        data_module=config,
        armistice_after_rounds=int(settings.get('armistice_rounds', DEFAULT_ARMISTICE_ROUNDS)),
    )
    turn_log = TurnLog()
    # The dice come from the same seed as everything else, so a seeded game replays exactly. `stats`
    # (deploys/kills/etc, per faction) feeds the Game Over report (server/report.py) -- purely
    # informational, so nothing about the game itself depends on it being attached.
    engine = GameEngine(gs, config, turn_log=turn_log, combat_rng=random.Random(rng.random()), stats=GameStats())
    budget = int(settings.get('dev', {}).get('bot_budget', DEFAULT_BUDGET))  # the heuristic bots' planning effort per pass
    bots = {}
    for a in assignments:
        if a['mode'] != 'BOT':
            continue
        bot_rng = random.Random(rng.random())
        if a['ai'] == 'random':
            bots[a['faction']] = RandomBot(engine, a['faction'], rng=bot_rng)
        elif a['ai'] == 'claude':
            # ClaudeBot's own __init__ (via _default_client()) raises RuntimeError right here, clearly,
            # if ANTHROPIC_API_KEY isn't set in the server's environment -- not silently, not deferred
            # to this seat's first turn.
            bots[a['faction']] = ClaudeBot(engine, a['faction'], rng=bot_rng)
        else:
            bots[a['faction']] = StrategyBot(engine, a['faction'], rng=bot_rng, budget=budget)
    seats = [dict(a, seat=i) for i, a in enumerate(assignments, 1)]
    return GameSession(engine, turn_log, bots, auto=auto), seats
