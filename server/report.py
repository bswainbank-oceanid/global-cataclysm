"""
Builds the Game Over report: one row per seat, everything the client's Game Over panel shows,
sorted by Victory status, then Strategic Centers, Territory MPC, Units produced, Units destroyed
(the tie-break order requested for the panel). Pure function of the finished game (engine, turn_log,
bots) -- no side effects, so it's cheap to call again and easy to unit-test on its own
(server/tests/test_report.py). Called once GameState.game_over is true; PhaseStepper and GameSession
each attach its result to every 'game_over' message they send (see their own _game_over_message).
"""
from engine.bots.strategy_bot import StrategyBot
from engine.economy import compute_income
from engine.state import FactionMode

# Victory status, best (lowest rank) first; a row with no status (Defensive/Neutral seats) sorts last.
_STATUS_RANK = {'Winner': 0, 'Armistice': 1, 'Forced to Surrender': 2, 'Surrendered': 3}

# elimination_reason labels for a forced surrender's grounds (surrender_grounds), SC loss quoted first
# per the report's own requested order; a self-surrender's is just ['Self-Surrender'], below.
_REASON_LABELS = {'strategic_center': 'SC Loss', 'income': 'Economic'}
_REASON_ORDER = ('strategic_center', 'income')


def _elimination_event(code, turn_log):
    """The self_surrender or surrender event that put `code` out, or None if it never was (also None,
    harmlessly, for a faction that was never eliminated at all -- callers only use this when
    FactionState.eliminated is already known true)."""
    for e in reversed(turn_log.events):
        if e['kind'] == 'self_surrender' and e['faction'] == code:
            return e
        if e['kind'] == 'surrender' and e['target'] == code:
            return e
    return None


def _elimination_reason(elim_event):
    """Why an eliminated faction is out, for the report: ['Self-Surrender'], or the forced-surrender
    grounds spelled out (['SC Loss'], ['Economic'], or both when a demand had both grounds at once) --
    None if `elim_event` is None (never eliminated, or the pre-surrender-rule fallback below fired)."""
    if elim_event is None:
        return None
    if elim_event['kind'] == 'self_surrender':
        return ['Self-Surrender']
    return [_REASON_LABELS[r] for r in _REASON_ORDER if r in elim_event['reasons']]


def _round_eliminated(elim_event):
    """The round (GameState.round_number, as it stood at the moment -- see record_surrender/
    record_self_surrender) the faction was put out, or None if `elim_event` is None. Older turn_log
    events (recorded before this field existed, e.g. a save from an earlier version) simply have no
    'round' key -- .get(...) rather than a KeyError."""
    return elim_event.get('round') if elim_event is not None else None


def _eliminated_by(elim_event):
    """Who eliminated this faction: the demander's code for a forced surrender ('surrender' event's own
    'faction'), or None -- nobody eliminated them but themselves for a self-surrender, and there is
    nobody at all for a faction still in the game."""
    if elim_event is None or elim_event['kind'] != 'surrender':
        return None
    return elim_event['faction']


def _victory_status(code, gs, active_at_end, armistice_participants, elim_event):
    """One of 'Winner', 'Armistice', 'Forced to Surrender', 'Surrendered' for a HUMAN/BOT seat; None
    for a Defensive/Neutral one (never a competitor, so it has no such thing as a victory status).

    Elimination is checked FIRST and always wins over 'Armistice': an eliminated faction that went on to
    propose (or was asked to agree to) an armistice among the survivors keeps the status that actually
    put it out (Surrendered/Forced to Surrender), never relabeled 'Armistice' -- that label is only for a
    faction that was still genuinely playing when the game stopped."""
    fstate = gs.factions[code]
    if fstate.mode not in (FactionMode.HUMAN, FactionMode.BOT):
        return None
    if fstate.eliminated:
        if elim_event is not None:
            return 'Surrendered' if elim_event['kind'] == 'self_surrender' else 'Forced to Surrender'
        return 'Forced to Surrender'  # defensive fallback; shouldn't be reachable now that elimination is surrender-only
    if armistice_participants is not None and code in armistice_participants:
        return 'Armistice'
    if code in active_at_end:
        return 'Winner'  # still in it when the game ended: the last one standing, or part of the winning alliance
    return 'Forced to Surrender'  # unreachable in practice: not eliminated, not active, no armistice


def _alliance_history(code, turn_log):
    """This faction's alliance timeline, one short line per change, in turn order -- 'Alliance history'
    in the report. Reads the whole game's turn_log (kept in full for exactly this)."""
    lines = []
    for e in turn_log.events:
        kind = e['kind']
        if kind == 'alliance_joined' and code in (e['faction'], e['target']):
            other = e['target'] if e['faction'] == code else e['faction']
            lines.append(f"T{e['turn']}: joined with {other}")
        elif kind == 'alliance_withdrawal' and e['faction'] == code:
            others = [m for m in e['former_members'] if m != code]
            lines.append(f"T{e['turn']}: left" + (f" ({', '.join(others)})" if others else ''))
        elif kind == 'alliance_withdrawal' and e['faction'] != code and code in e['former_members']:
            lines.append(f"T{e['turn']}: {e['faction']} left")
    return lines


def _bot_info(bot):
    """(Bot-Type, Bot Strategy) for a seat's bot instance -- ('Strategy', its drawn base style) for a
    StrategyBot, ('Random', None) for a plain RandomBot, (None, None) for a HUMAN/DEFENSIVE/NEUTRAL seat
    (no bot object at all)."""
    if bot is None:
        return None, None
    if isinstance(bot, StrategyBot):
        return 'Strategy', bot.base_style
    return 'Random', None


def build_game_report(engine, turn_log, bots):
    """[{faction, seat_type, victory_status, elimination_reason, strategic_centers, territory_mpc,
    units_produced, units_destroyed, alliance_history, bot_type, bot_strategy, alliance_strategy,
    alliance_behavior, rounds_in_game, round_eliminated, eliminated_by}, ...] -- one row per seated
    faction (all 6, Defensive/Neutral included), sorted by the report's own order: Victory status, then
    Strategic Centers, Territory MPC, Units produced, Units destroyed, each descending (a None victory
    status -- Defensive/Neutral -- sorts after every real one), a final alphabetical tie-break.
    elimination_reason is only for an eliminated faction (why it's out -- see _elimination_reason), None
    for anyone still in the game and for Defensive/Neutral seats. Units produced/destroyed are 0 if
    `engine.stats` is None (a GameStats wasn't attached) rather than an error -- purely cosmetic, never
    required for the game itself to run. rounds_in_game (GameState.round_number, the SAME value on every
    row) is how long the whole game ran; round_eliminated (per row, None unless the faction was actually
    eliminated) is which round it happened in -- both read from the authoritative, monotonically-
    incrementing round counter (see GameState.round_number / GameEngine.advance_turn), not derived after
    the fact from global_turn, which would give wrong answers for earlier eliminations once later ones
    have shrunk active_factions(). eliminated_by (per row) is who forced a demanded surrender (None for a
    self-surrender -- nobody eliminated them but themselves -- and for anyone never eliminated)."""
    gs = engine.game_state
    data = engine.data
    terrs = data.territories()
    stats = engine.stats
    active_at_end = set(gs.active_factions())

    armistice = next((e for e in reversed(turn_log.events) if e['kind'] == 'armistice'), None)
    armistice_participants = set(armistice['participants']) if armistice is not None else None

    rows = []
    for code, fstate in gs.factions.items():
        sc_count = sum(1 for tid, t in gs.territories.items()
                       if t.owner == code and terrs[tid]['type'] == 'land' and gs.is_strategic_center(tid, terrs[tid]))
        bot_type, bot_strategy = _bot_info(bots.get(code))
        elim_event = _elimination_event(code, turn_log) if fstate.eliminated else None
        rows.append({
            'faction': code,
            'seat_type': fstate.mode.value,
            'victory_status': _victory_status(code, gs, active_at_end, armistice_participants, elim_event),
            'elimination_reason': _elimination_reason(elim_event),
            'strategic_centers': sc_count,
            'territory_mpc': compute_income(code, gs, data),
            'units_produced': sum(q for (f, _), q in stats.deployed.items() if f == code) if stats else 0,
            'units_destroyed': sum(q for (f, _), q in stats.kills.items() if f == code) if stats else 0,
            'alliance_history': _alliance_history(code, turn_log),
            'bot_type': bot_type,
            'bot_strategy': bot_strategy,
            'alliance_strategy': fstate.alliance_strategy,
            'alliance_behavior': fstate.alliance_behavior,
            'rounds_in_game': gs.round_number,
            'round_eliminated': _round_eliminated(elim_event),
            'eliminated_by': _eliminated_by(elim_event),
        })

    rows.sort(key=lambda r: (
        _STATUS_RANK.get(r['victory_status'], len(_STATUS_RANK)),
        -r['strategic_centers'], -r['territory_mpc'], -r['units_produced'], -r['units_destroyed'],
        r['faction'],
    ))
    return rows
