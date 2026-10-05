"""
Saving a live game so it survives a server restart (docs/DATA_MODEL.md, "Game save").

A game is saved as a SNAPSHOT taken at the start of a faction's turn -- the one moment nothing is in
flight: no bot plan half used, nothing staged, no battle queued -- plus every message the game has
handled since (the DECISIONS: purchases, moves, diplomacy, answers, surrenders, "next"). Loading it
rebuilds the game from the snapshot and replays the decisions through the same code that handled them
the first time. Bots and dice draw from random generators whose states are in the snapshot, so the
replay comes out exactly as the game went (what tools/golden_games.py relies on too).

    game = RecordedGame(session, setup)        # wrap a freshly built GameSession
    game.handle_message(msg)                   # instead of session.handle_message
    doc = game.doc()                           # JSON-ready; store it (games.save_state)
    game = load(doc)                           # after a restart: the same game, at the same point

`setup` is what rebuilding needs beyond the snapshot: {'seats': lobby.build_session's seats,
'settings': the game's settings, 'generated': a New Scenario's generated modules or None,
'scenario_id': its scenario id or None}.

Limits: orders a human has staged but not confirmed are part of the decisions, so they come back too;
a Claude bot (engine/bots/claude_bot.py) asks the API again on a replay and may decide differently.
Data edited between saving and loading (unit stats, say) is used as it now is, except a New
Scenario's generated modules, which are saved with the game.
"""
import copy
import dataclasses
import random

from engine import data as data_module
from engine.bots.random_bot import RandomBot
from engine.bots.strategy_bot import StrategyBot
from engine.bots.strategy_log import StrategyLogger
from engine.engine import GameEngine
from engine.game_config import GameConfig
from engine.repository import OverlayRepository, default_repository
from engine.state import GameState
from engine.stats import GameStats
from engine.turn_log import TurnLog

from .session import GameSession
from .stepper import START_OF_TURN

VERSION = 1
NOT_RECORDED = ('join',)  # pure queries: they change nothing, so a replay needn't see them

# The engine's per-turn bookkeeping, all sets of faction codes (empty at a turn start, saved regardless).
ENGINE_SETS = ('_purchases_confirmed', '_combat_moves_confirmed', '_combat_resolved',
               '_noncombat_moves_confirmed', '_return_to_base_processed', '_alliance_action_taken')
ENGINE_STAGED = ('_staged_purchases', '_staged_combat_moves', '_staged_noncombat_moves')


class RecordedGame:
    """A GameSession that keeps its own save: the latest turn-start snapshot and the decisions since.
    Anything not defined here (engine, stepper, connect, ...) is the session's own."""

    def __init__(self, session, setup, snapshot_doc=None, decisions=None):
        self.session = session
        self.setup = setup
        self.decisions = list(decisions or [])
        self.snapshot = snapshot_doc if snapshot_doc is not None else take_snapshot(session)
        self._turn_key = self._current_turn_key()
        self.snapshots_taken = 0 if snapshot_doc is not None else 1

    def __getattr__(self, name):
        return getattr(self.session, name)

    def handle_message(self, msg):
        out = self.session.handle_message(msg)
        if msg.get('type') not in NOT_RECORDED:
            self.decisions.append(copy.deepcopy(msg))
        key = self._current_turn_key()
        if key is not None and key != self._turn_key:  # a new turn is about to start: a clean moment
            self.snapshot = take_snapshot(self.session)
            self.decisions = []
            self._turn_key = key
            self.snapshots_taken += 1
        return out

    def doc(self):
        """The whole save, JSON-ready."""
        return {'version': VERSION, 'setup': self.setup, 'snapshot': self.snapshot, 'decisions': self.decisions}

    def _current_turn_key(self):
        queue = self.session.stepper._queue
        if queue is None or queue.get('phase') != START_OF_TURN:
            return None
        return [queue['faction'], self.session.engine.game_state.global_turn]


def load(doc):
    """The RecordedGame `doc` (RecordedGame.doc()) saved: rebuilt from its snapshot, then its decisions
    replayed. Raises ValueError for a save this server can't read."""
    if doc.get('version') != VERSION:
        raise ValueError(f"a game save of version {doc.get('version')!r} can't be loaded (this server reads {VERSION})")
    session = restore_snapshot(doc['snapshot'], doc['setup'])
    game = RecordedGame(session, doc['setup'], snapshot_doc=doc['snapshot'])
    # (the queue is planned again by the first message, as every stepper entry point does when it has
    # none: at a turn start that is just the Start of Turn announcement, which draws nothing random)
    for msg in doc['decisions']:
        game.handle_message(msg)
    return game


# ---- the snapshot ------------------------------------------------------------------------------------

def take_snapshot(session):
    """Everything a game holds at a turn start that rebuilding it needs, JSON-ready."""
    engine, stepper = session.engine, session.stepper
    for name in ENGINE_STAGED:
        if getattr(engine, name):
            raise RuntimeError(f'a snapshot must be taken with nothing staged ({name} is not empty)')
    bots = {}
    for faction, bot in session.bots.items():
        entry = {'kind': _bot_kind(bot), 'rng': _rng_state(bot.rng)}
        if isinstance(bot, StrategyBot):
            entry.update(base_style=bot.base_style, budget=bot.budget)
        bots[faction] = entry
    logs = {f: {'start': log._start, 'last_start': log._last_start} for f, log in stepper._strategy_logs.items()}
    armistice = session._armistice
    if armistice is not None:
        armistice = {'from': armistice['from'], 'pending': sorted(armistice['pending']),
                     'accepted': sorted(armistice['accepted'])}
    return {
        'game_state': engine.game_state.to_dict(),
        'combat_rng': _rng_state(engine._combat_rng),
        'engine': {name: sorted(getattr(engine, name)) for name in ENGINE_SETS},
        'turn_log': copy.deepcopy(session.turn_log.events),
        'stats': _stats_doc(engine.stats),
        'bots': bots,
        'strategy_logs': logs,
        'session': {'pending_invite': copy.deepcopy(session._pending_invite), 'armistice': armistice,
                    'armistice_cooldown': [[k, v] for k, v in session._armistice_cooldown.items()],
                    'auto_armistice_round': session._auto_armistice_round},
    }


def restore_snapshot(snap, setup):
    """The GameSession `snap` (take_snapshot) describes, with its queue not yet planned."""
    config = _config(setup)
    gs = GameState.from_dict(copy.deepcopy(snap['game_state']))
    turn_log = TurnLog(events=copy.deepcopy(snap['turn_log']))
    stats = _stats_from(snap['stats'])
    engine = GameEngine(gs, config, turn_log=turn_log, combat_rng=_rng(snap['combat_rng']), stats=stats)
    for name in ENGINE_SETS:
        setattr(engine, name, set(snap['engine'][name]))
    bots = {}
    for faction, entry in snap['bots'].items():
        bots[faction] = _bot(engine, faction, entry)
    session = GameSession(engine, turn_log, bots)
    s = snap['session']
    session._pending_invite = copy.deepcopy(s['pending_invite'])
    a = s['armistice']
    session._armistice = None if a is None else {'from': a['from'], 'pending': set(a['pending']),
                                                 'accepted': set(a['accepted'])}
    session._armistice_cooldown = {k: v for k, v in s['armistice_cooldown']}
    session._auto_armistice_round = s['auto_armistice_round']
    for faction, entry in snap['strategy_logs'].items():
        log = StrategyLogger(engine, bots[faction])
        log._start, log._last_start = entry['start'], entry['last_start']
        session.stepper._strategy_logs[faction] = log
    return session


def _config(setup):
    generated = setup.get('generated')
    if not generated:
        return data_module
    return GameConfig(setup['scenario_id'], OverlayRepository(default_repository(), copy.deepcopy(generated)))


def generated_modules(session):
    """A New Scenario game's generated modules (to save with it), or None for a fixed scenario's."""
    repo = getattr(session.engine.data, 'repo', None)
    if isinstance(repo, OverlayRepository):
        return copy.deepcopy(list(repo._docs.values()))
    return None


def setup_for(session, seats, settings):
    """The `setup` RecordedGame needs, for a session lobby.build_session just built from `settings`."""
    data = session.engine.data
    generated = generated_modules(session)
    return {'seats': copy.deepcopy(seats), 'settings': copy.deepcopy(settings), 'generated': generated,
            'scenario_id': data.scenario_id if generated else None}


# ---- bots --------------------------------------------------------------------------------------------

def _bot_kind(bot):
    if isinstance(bot, StrategyBot):
        return 'strategy'
    if type(bot).__name__ == 'ClaudeBot':
        return 'claude'
    if isinstance(bot, RandomBot):
        return 'random'
    raise TypeError(f"a {type(bot).__name__} can't be saved")


def _bot(engine, faction, entry):
    kind = entry['kind']
    if kind == 'strategy':
        bot = StrategyBot(engine, faction, rng=random.Random(0), budget=entry['budget'])
        bot.base_style = bot.style = entry['base_style']
    elif kind == 'claude':
        from engine.bots.claude_bot import ClaudeBot  # (needs ANTHROPIC_API_KEY, like at game start)
        bot = ClaudeBot(engine, faction, rng=random.Random(0))
    else:
        bot = RandomBot(engine, faction, rng=random.Random(0))
    bot.rng.setstate(_rng(entry['rng']).getstate())
    return bot


# ---- random generators, stats ------------------------------------------------------------------------

def _rng_state(rng):
    version, internal, gauss = rng.getstate()
    return [version, list(internal), gauss]


def _rng(state):
    rng = random.Random()
    rng.setstate((state[0], tuple(state[1]), state[2]))
    return rng


def _stats_doc(stats):
    if stats is None:
        return None
    out = {}
    for f in dataclasses.fields(stats):
        value = getattr(stats, f.name)
        if isinstance(value, dict):  # keys may be tuples or ints: kept as [key, value] pairs
            value = [[list(k) if isinstance(k, tuple) else k, v] for k, v in value.items()]
        out[f.name] = copy.deepcopy(value)
    return out


def _stats_from(doc):
    if doc is None:
        return None
    stats = GameStats()
    for f in dataclasses.fields(stats):
        if f.name not in doc:
            continue
        value = doc[f.name]
        if isinstance(getattr(stats, f.name), dict):
            value = {tuple(k) if isinstance(k, list) else k: v for k, v in value}
        setattr(stats, f.name, copy.deepcopy(value))
    return stats
