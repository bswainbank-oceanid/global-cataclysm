"""
Seeded full-game digests, for proving a refactor left the game unchanged.

Plays a fixed set of seeded bot games (random and strategy bots, with and without
Defensive/Neutral seats and starting alliances) and prints a SHA-1 of each game's
full turn log plus the final board. `--save` writes them to a file; `--check`
compares against a saved file and exits non-zero on any difference.

    python tools/golden_games.py --save tools/golden_games.json
    python tools/golden_games.py --check tools/golden_games.json
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.bots.driver import play_to_completion  # noqa: E402
from server.lobby import build_session  # noqa: E402


def _bot(ai, faction='random', alliance=0):
    return {'mode': 'BOT', 'faction': faction, 'alliance': alliance, 'strategy': 'random', 'behavior': 'random', 'ai': ai}


def _seat(mode, faction='random'):
    return {'mode': mode, 'faction': faction}


CASES = {
    'random_6bot': ({'seats': [_bot('random') for _ in range(6)], 'seed': 11}, 60),
    'strategy_6bot': ({'seats': [_bot('strategy') for _ in range(6)], 'seed': 12}, 18),
    'strategy_3bot_defensive': ({'seats': [_bot('strategy'), _bot('strategy'), _bot('strategy'),
                                           _seat('DEFENSIVE'), _seat('DEFENSIVE'), _seat('NEUTRAL')],
                                 'seed': 13, 'max_alliance_size': 1}, 24),
    'mixed_alliances': ({'seats': [_bot('strategy', alliance=1), _bot('strategy', alliance=1), _bot('random'),
                                   _bot('random'), _seat('DEFENSIVE'), _bot('strategy')],
                         'seed': 14, 'allow_combat_first_turn': True}, 24),
    'fixed_order_2bot': ({'seats': [_bot('random', 'NAA'), _bot('strategy', 'GPC'), _seat('NEUTRAL'), _seat('NEUTRAL'),
                                    _seat('DEFENSIVE'), _seat('DEFENSIVE')],
                          'seed': 15, 'randomize_order': False}, 30),
}


def digest(settings, turns):
    session, _ = build_session(settings)
    initial = json.dumps(session.engine.game_state.to_dict(), sort_keys=True)
    play_to_completion(session.engine, session.bots, max_turns=turns)
    log = '\n'.join(json.dumps(e, sort_keys=True) for e in session.turn_log.events)
    final = json.dumps(session.engine.game_state.to_dict(), sort_keys=True)
    return {
        'setup': hashlib.sha1(initial.encode()).hexdigest(),
        'log': hashlib.sha1(log.encode()).hexdigest(),
        'final': hashlib.sha1(final.encode()).hexdigest(),
        'events': len(session.turn_log.events),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--save')
    ap.add_argument('--check')
    ap.add_argument('--only')
    args = ap.parse_args()
    results = {}
    for name, (settings, turns) in CASES.items():
        if args.only and name != args.only:
            continue
        results[name] = digest(settings, turns)
        print(name, results[name], flush=True)
    if args.save:
        with open(args.save, 'w') as f:
            json.dump(results, f, indent=2)
            f.write('\n')
    if args.check:
        with open(args.check) as f:
            expected = json.load(f)
        bad = [n for n in results if results[n] != expected.get(n)]
        for n in bad:
            print(f'MISMATCH {n}: expected {expected.get(n)}, got {results[n]}')
        print('golden games:', 'all match' if not bad else f'{len(bad)} differ')
        sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
