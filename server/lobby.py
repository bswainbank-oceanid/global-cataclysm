"""
The game launch screen's server side: validate a set of launch settings and build
the game they describe.

Settings (the "new_game" message's "settings"):

    {"seats": [ {"mode": "HUMAN" | "BOT" | "DEFENSIVE" | "NEUTRAL",
                 "faction": "random" | "NAA" | "UE" | "UER" | "GPC" | "PAF" | "AAC",
                 "alliance": 0 | 1 | 2 | 3,             # 0 = none; players only
                 "strategy": "random" | aggressive | passive | counterweight | independent | variable,   # bots only
                 "behavior": "random" | loyal | opportunistic | treacherous | variable},                  # bots only
                ... exactly six ...],
     "randomize_order": true,                            # default true
     "can_withdraw": true,                               # players may leave an alliance (default true)
     "can_rejoin": false,                                # ...and may re-ally with those they left (default false)
     "dev": {"combat_first_turn": false}}                # optional, testing only

Every faction is in exactly one seat: explicit picks are honoured first (and must
be distinct), "random" seats then take the factions left over, in random order.
"Players" are the HUMAN and BOT seats; DEFENSIVE seats' units defend but take no
turns, and NEUTRAL seats are impassable (see data/rules.json power_modes).

The rules the screen enforces (and the server re-checks, being the authority):
at least two players, at most one human, distinct explicit factions, and each
starting alliance (seats sharing an alliance number) has two or more members and
does not include every player. Bots' strategy/behavior "random" is resolved once
by the engine at game start. All problems are reported together in
LobbyError.problems.
"""
import random

from engine import data as data_module
from engine.bots.alliance_policy import BEHAVIORS, STRATEGIES
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode
from engine.turn_log import TurnLog
from .session import GameSession

SEAT_COUNT = 6
MODES = ('HUMAN', 'BOT', 'DEFENSIVE', 'NEUTRAL')
ALLIANCE_NUMBERS = (1, 2, 3)
PLAYER_MODES = ('HUMAN', 'BOT')


class LobbyError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


def check_settings(settings):
    """Every problem with `settings`, as a list of readable strings (empty = valid)."""
    problems = []
    seats = settings.get('seats')
    if not isinstance(seats, list) or len(seats) != SEAT_COUNT:
        return [f'there must be exactly {SEAT_COUNT} seats']
    factions = list(data_module.factions())

    picked = {}
    for i, seat in enumerate(seats, 1):
        if seat.get('mode') not in MODES:
            problems.append(f'seat {i}: mode must be one of {", ".join(MODES)}')
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
            if seat.get('strategy', 'random') not in ('random',) + tuple(STRATEGIES):
                problems.append(f'seat {i}: unknown alliance strategy {seat.get("strategy")!r}')
            if seat.get('behavior', 'random') not in ('random',) + tuple(BEHAVIORS):
                problems.append(f'seat {i}: unknown alliance behavior {seat.get("behavior")!r}')

    for key in ('randomize_order', 'can_withdraw', 'can_rejoin'):
        if key in settings and not isinstance(settings[key], bool):
            problems.append(f'{key} must be true or false')

    players = [i for i, s in enumerate(seats, 1) if s.get('mode') in PLAYER_MODES]
    humans = [i for i, s in enumerate(seats, 1) if s.get('mode') == 'HUMAN']
    if len(players) < 2:
        problems.append('at least two players (humans or bots) are needed')
    if len(humans) > 1:
        problems.append('at most one human player is supported')

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
    return problems


def resolve_settings(settings, rng=None):
    """Validates `settings` and resolves the random faction picks. Returns
    (assignments, groups, randomize_order): assignments is one dict per seat
    ({faction, mode, strategy, behavior}, in seat order), groups the starting
    alliances as lists of faction codes. Raises LobbyError."""
    problems = check_settings(settings)
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
        })
    by_number = {}
    for seat, a in zip(seats, assignments):
        n = int(seat.get('alliance') or 0)
        if n and seat['mode'] in PLAYER_MODES:
            by_number.setdefault(n, []).append(a['faction'])
    return assignments, [g for _, g in sorted(by_number.items())], bool(settings.get('randomize_order', True))


def build_session(settings, rng=None):
    """The GameSession for `settings`, plus the resolved seat list for the client.
    Returns (session, seats); raises LobbyError."""
    rng = rng or random.Random()
    assignments, groups, randomize = resolve_settings(settings, rng)
    modes = {a['faction']: FactionMode[a['mode']] for a in assignments}
    gs = build_game_state(
        'starting_setup_200ipc', modes, randomize_play_order=randomize, rng=rng,
        max_alliance_size=max([2] + [len(g) for g in groups]),
        alliance_strategies={a['faction']: a['strategy'] for a in assignments if a['mode'] == 'BOT'},
        alliance_behaviors={a['faction']: a['behavior'] for a in assignments if a['mode'] == 'BOT'},
        starting_alliances=groups,
        can_withdraw_from_alliances=bool(settings.get('can_withdraw', True)),
        can_rejoin_alliances=bool(settings.get('can_rejoin', False)),
        allow_combat_moves_first_turn=bool(settings.get('dev', {}).get('combat_first_turn', False)),
    )
    turn_log = TurnLog()
    engine = GameEngine(gs, data_module, turn_log=turn_log)
    bots = {a['faction']: RandomBot(engine, a['faction'], rng=random.Random(rng.random()))
            for a in assignments if a['mode'] == 'BOT'}
    seats = [dict(a, seat=i) for i, a in enumerate(assignments, 1)]
    return GameSession(engine, turn_log, bots), seats
