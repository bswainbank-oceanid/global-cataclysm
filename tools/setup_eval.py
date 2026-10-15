"""
Bot evaluation: plays a saved scenario setup engine-only, bots on every playing seat, many times over, and
reports who won and how -- by faction, by strategy profile (the bot's play style: Strategic, Defensive,
Expansive, Controlling, Variable), and by type of victory:

    python tools/setup_eval.py --setup Setup_002 --games 100 [--workers 14] [--seed 1] [--budget 3500] [--alt-seats 2]

The setup is read from the server's database (server_data/global_cataclysm.sqlite3: the shared scenarios,
as saved there), or else data/modules/scenario_setup/<id>.json; a HUMAN seat is played by a bot like the rest.
A game ends as a live one does (with up to six bots, and alliances if the setup allows them): by surrender down to the last faction standing (or an alliance of all that
are left), or by the automatic armistice once its 'armistice_rounds' have been played (the bots always
accept it). At an armistice the faction AHEAD wins, ranked as the game report ranks: the most Strategic
Centers, then the most MCP (income: its uncontested territories' value), then the most unit value. Types of
victory:
    SC          the loser was forced to surrender for Strategic Center loss (with or without Economic)
    Economic    forced to surrender for income alone
    Armistice   ahead when the armistice ended the game
    Alliance    every faction left is allied: they all win (each counted as a winner)
A seat's bot type 'strategy_alt' (or --alt-seats: those seats, numbered from 1) plays the alternate set of strategy profiles (engine/game_config.py's bot sets):
set one seat to it in the setup and the report compares the two sets head to head. Writes every game's
details to exports/eval/<setup>_<games>g.json.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, 'server_data', 'global_cataclysm.sqlite3')


def load_setup(setup_id):
    """The setup's settings: the server database's shared scenario of that id (or name), else the file."""
    if os.path.exists(DB):
        db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
        try:
            for (doc,) in db.execute('SELECT doc FROM shared_scenarios'):
                d = json.loads(doc)
                if setup_id in (d.get('id'), d.get('name')):
                    return d['name'], d['settings'], 'database'
        finally:
            db.close()
    path = os.path.join(ROOT, 'data', 'modules', 'scenario_setup', f'{setup_id}.json')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    return d['name'], d['settings'], 'file'


def play(args):
    """One game: its winner, how it was won, and every playing faction's standing at the end."""
    settings, seed, budget, alt_seats = args
    from engine.bots.driver import play_to_completion
    from engine.economy import compute_income
    from server.lobby import build_session

    settings = json.loads(json.dumps(settings))
    for i, seat in enumerate(settings['seats'], 1):
        if seat.get('mode') == 'HUMAN':
            seat['mode'] = 'BOT'
        if i in alt_seats and seat.get('mode') == 'BOT':
            seat['ai'] = 'strategy_alt'
    settings['seed'] = seed
    settings.setdefault('dev', {})['bot_budget'] = budget
    session, _ = build_session(settings)
    engine, gs, log = session.engine, session.engine.game_state, session.turn_log
    armistice_after = int(settings.get('armistice_rounds') or 0)
    players = [f for f in gs.factions if f in session.bots]
    start = time.time()
    armistice = False
    while not gs.game_over:
        if armistice_after and gs.round_number > armistice_after:  # (proposed as that round begins: bots accept)
            engine.end_by_armistice(None, gs.active_factions(), automatic_after=armistice_after)
            armistice = True
            break
        if play_to_completion(engine, session.bots, max_turns=1) == 0:
            break
    terrs = engine.data.territories()
    units = engine.data.units()
    standing = {}
    for f in players:
        standing[f] = {
            'profile': session.bots[f].base_style,
            'bot_set': getattr(session.bots[f], 'bot_set', None),
            'alive': not gs.factions[f].eliminated,
            'income': compute_income(f, gs, engine.data) if not gs.factions[f].eliminated else 0,
            'scs': len(engine._controlled_scs(f)),
            'unit_value': sum(units[u.unit_type]['cost'] or 0 for t in gs.territories.values() for u in t.units if u.owner == f),
            'territories': sum(1 for tid, t in gs.territories.items() if t.owner == f and terrs[tid]['type'] == 'land'),
        }
    alive = [f for f in players if standing[f]['alive']]
    surrenders = [e for e in log.events if e['kind'] == 'surrender']
    if armistice:
        kind = 'Armistice'
        winner = max(alive, key=lambda f: (standing[f]['scs'], standing[f]['income'], standing[f]['unit_value']))
        tied = [f for f in alive if (standing[f]['scs'], standing[f]['income']) ==
                (standing[winner]['scs'], standing[winner]['income'])]
        winners = [winner]
    elif len(alive) > 1:  # (the game over with more than one left: an alliance of all that remain won together)
        kind, winners, winner, tied = 'Alliance', sorted(alive), None, []
    else:
        winner = alive[0] if alive else None
        winners = [winner] if winner else []
        last = surrenders[-1] if surrenders else None
        kind = ('SC' if last and 'strategic_center' in last['reasons'] else 'Economic' if last else 'Other')
        tied = [winner]
    return {'seed': seed, 'winner': winner, 'winners': winners,
            'profile': standing[winner]['profile'] if winner else None,
            'bot_set': standing[winner]['bot_set'] if winner else None,
            'kind': kind, 'both_grounds': bool(surrenders and set(surrenders[-1]['reasons']) >= {'strategic_center', 'income'}),
            'close_call': len(tied) > 1, 'round': gs.round_number, 'seconds': round(time.time() - start, 1),
            'standing': standing}


def pct(n, total):
    return f'{100.0 * n / total:5.1f}%' if total else '  -  '


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--setup', default='Setup_002')
    ap.add_argument('--games', type=int, default=100)
    ap.add_argument('--seed', type=int, default=1, help='the first game\'s seed (then +1 each game)')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument('--budget', type=int, default=3500, help='the bots\' planning effort (the live default: 3500)')
    ap.add_argument('--alt-seats', default='', help='seats (from 1, comma-separated) whose bots play the alternate set')
    a = ap.parse_args(argv)
    name, settings, source = load_setup(a.setup)
    alt_seats = {int(x) for x in a.alt_seats.split(',') if x.strip()}
    print(f'{a.setup} "{name}" (from the {source}): {a.games} games, {a.workers} at a time', flush=True)
    started = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for r in pool.map(play, [(settings, a.seed + g, a.budget, alt_seats) for g in range(a.games)]):
            results.append(r)
            who = ', '.join(f"{w} ({r['standing'][w]['profile']}, {r['standing'][w]['bot_set']})" for w in r['winners']) or 'nobody'
            s = r['standing'].get(r['winner'], {})
            print(f"game {len(results):3d} (seed {r['seed']}): {who} by {r['kind']}, round {r['round']}"
                  + (f", SCs {s.get('scs')} MCP {s.get('income')} UV {s.get('unit_value')}" if s else '') + f"  [{r['seconds']}s]", flush=True)

    n = len(results)
    print(f'\n{n} games in {time.time() - started:.0f}s\n')
    def won_by(r):  # each winner's standing (an alliance's members all count)
        return [r['standing'][w] for w in r['winners']]

    for title, key in (('Winning faction (each alliance member counts)', 'winner'), ('Winning strategy profile', 'profile'),
                       ('Winning bot set', 'bot_set'), ('Type of victory', 'kind')):
        if key == 'kind':
            counts = collections.Counter(r['kind'] for r in results)
        elif key == 'winner':
            counts = collections.Counter(w for r in results for w in r['winners'])
        else:
            counts = collections.Counter(s[key] for r in results for s in won_by(r))
        print(title)
        for value, c in counts.most_common():
            print(f'  {str(value):<12} {c:4d}  {pct(c, n)}')
        print()
    # how often each faction / profile was in the game at all, to read the win rates against
    seated = collections.Counter(f for r in results for f in r['standing'])
    profiles = collections.Counter(s['profile'] for r in results for s in r['standing'].values())
    print('Win rate when playing (wins / games played)')
    for f, c in seated.most_common():
        w = sum(1 for r in results if f in r['winners'])
        print(f'  {f:<12} {w:3d}/{c:<3d} {pct(w, c)}')
    for p, c in profiles.most_common():
        w = sum(1 for r in results for s in won_by(r) if s['profile'] == p)
        print(f'  {p:<12} {w:3d}/{c:<3d} {pct(w, c)}')
    sets = collections.Counter(s['bot_set'] for r in results for s in r['standing'].values())
    if len(sets) > 1:  # (the alternate strategy profiles in play: the two sets head to head)
        print('\nBy bot set (wins / games played)')
        for b, c in sets.most_common():
            w = sum(1 for r in results for s in won_by(r) if s['bot_set'] == b)
            print(f'  {b:<12} {w:3d}/{c:<3d} {pct(w, c)}')
            for p in sorted({s['profile'] for r in results for s in r['standing'].values() if s['bot_set'] == b}):
                played = sum(1 for r in results for s in r['standing'].values() if s['bot_set'] == b and s['profile'] == p)
                won = sum(1 for r in results for s in won_by(r) if s['bot_set'] == b and s['profile'] == p)
                print(f'    {p:<10} {won:3d}/{played:<3d} {pct(won, played)}')
    if len(sets) > 1:  # each faction's win rate in each set's hands
        print('\nBy faction and bot set (wins / games played)')
        for f in sorted(seated):
            cells = []
            for b in sorted(sets):
                played = sum(1 for r in results if f in r['standing'] and r['standing'][f]['bot_set'] == b)
                won = sum(1 for r in results if f in r['winners'] and r['standing'][f]['bot_set'] == b)
                cells.append(f'{b} {won:2d}/{played:<3d} {pct(won, played)}')
            print(f'  {f:<6} ' + '   '.join(cells))
    print(f"\nSurrenders on both SC and Economic grounds (counted as SC): {sum(r['both_grounds'] for r in results)}")
    print(f"Armistices level on SCs and MCP (decided by unit value): {sum(r['close_call'] for r in results)}")
    out = os.path.join(ROOT, 'exports', 'eval', f'{a.setup}_{n}g.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'setup': a.setup, 'name': name, 'source': source, 'games': results}, f, indent=1)
    print(f'details: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
