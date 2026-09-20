"""
Plays engine-only games between the bot AIs and reports how each did -- the baseline test for the
strategy bots (engine/bots/strategy_bot.py) against the random bots.

    python tools/bot_arena.py --games 10 --mix strategy=1,random=5
    python tools/bot_arena.py --games 6 --mix strategy=3,random=3 --budget 1500 --seed 100

--mix says how many of the six seats each AI gets (they must add up to at most six; the rest are empty
seats' worth of Neutral). Factions are dealt to the seats at random each game. A faction 'survives' if it
is not eliminated when the game ends (it ended by an alliance of the survivors, by one faction left, or
by the turn cap).
"""
import argparse
import collections
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.bots.driver import play_to_completion  # noqa: E402
from server.lobby import build_session  # noqa: E402


def parse_mix(text):
    mix = collections.OrderedDict()
    for part in text.split(','):
        name, count = part.split('=')
        mix[name.strip()] = int(count)
    if sum(mix.values()) > 6 or sum(mix.values()) < 2:
        raise SystemExit('the mix must fill between 2 and 6 seats')
    return mix


def play(seed, mix, max_turns, budget):
    seats = []
    for ai, n in mix.items():
        for _ in range(n):
            seats.append({'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': ai})
    while len(seats) < 6:
        seats.append({'mode': 'NEUTRAL', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random'})
    session, assigned = build_session({'seats': seats, 'seed': seed, 'dev': {'bot_budget': budget}})
    engine, gs = session.engine, session.engine.game_state
    turns = play_to_completion(engine, session.bots, max_turns=max_turns)
    ai_of = {a['faction']: a['ai'] for a in assigned if a['mode'] == 'BOT'}
    terrs = engine.data.territories()
    result = {'turns': turns, 'over': gs.game_over, 'factions': {}}
    for f, ai in ai_of.items():
        fs = gs.factions[f]
        scs = sum(1 for tid, t in gs.territories.items() if t.owner == f and terrs[tid]['type'] == 'land' and gs.is_strategic_center(tid, terrs[tid]))
        terr = sum(1 for t in gs.territories.values() if t.owner == f and terrs[t.territory_id]['type'] == 'land')
        units = sum(1 for t in gs.territories.values() for u in t.units if u.owner == f)
        style = getattr(session.bots[f], 'base_style', None)
        result['factions'][f] = {'ai': ai, 'alive': not fs.eliminated, 'scs': scs, 'territories': terr, 'units': units, 'style': style}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--games', type=int, default=10)
    ap.add_argument('--mix', type=parse_mix, default=parse_mix('strategy=1,random=5'))
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--max-turns', type=int, default=400)
    ap.add_argument('--budget', type=int, default=1500, help='strategy bots: simulated battles per planning pass')
    args = ap.parse_args()

    totals = collections.defaultdict(lambda: collections.Counter())
    seats_played = collections.Counter()
    by_style = collections.defaultdict(lambda: collections.Counter())
    started = time.time()
    finished = 0
    for g in range(args.games):
        r = play(args.seed + g, args.mix, args.max_turns, args.budget)
        finished += bool(r['over'])
        alive_ai = {f['ai'] for f in r['factions'].values() if f['alive']}
        line = ', '.join(f"{n}:{f['ai'][0]}{'+' if f['alive'] else '-'}{f['scs']}" for n, f in r['factions'].items())
        print(f"game {g + 1}: {r['turns']} turns, {'over' if r['over'] else 'CAP'}; {line}", flush=True)
        for f in r['factions'].values():
            t = totals[f['ai']]
            seats_played[f['ai']] += 1
            t['survived'] += f['alive']
            t['scs'] += f['scs']
            t['territories'] += f['territories']
            t['units'] += f['units']
            if f['ai'] == 'strategy':
                by_style[f['style']]['seats'] += 1
                by_style[f['style']]['survived'] += f['alive']
        for ai in alive_ai:
            totals[ai]['games_with_survivor'] += 1
    print()
    print(f'{args.games} games ({finished} ended, the rest hit the turn cap) in {time.time() - started:.0f}s; mix {dict(args.mix)}')
    for ai, t in totals.items():
        n = seats_played[ai]
        print(f"  {ai:9} seats {n:3}  survived {t['survived']:3} ({t['survived'] / n:4.0%})  "
              f"avg SCs {t['scs'] / n:4.1f}  avg territories {t['territories'] / n:4.1f}  avg units {t['units'] / n:5.1f}  "
              f"games with a survivor {t['games_with_survivor']}/{args.games}")
    if by_style:
        print('  strategy bots by style:', ', '.join(f"{s} {c['survived']}/{c['seats']}" for s, c in sorted(by_style.items())))


if __name__ == '__main__':
    main()
