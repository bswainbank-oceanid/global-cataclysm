"""
Runs a full NAA-vs-AAC bot game (the other four factions left NEUTRAL) to
completion, then prints a stats.GameStats report -- territory captures in
turn order, and per-faction/per-unit-type deployed/promotions/deaths/
kills (with transport-form deaths called out). A manual demo/verification
script, not part of the test suite (test_random_bot.py's
TestPlayToCompletion exercises the same path with a small turn cap, for
speed).

Run: python -m engine.bots.run_game [--seed N] [--max-turns N]
"""
import argparse
import random

from .. import data
from ..engine import GameEngine
from ..setup import build_game_state
from ..state import PowerMode
from ..stats import GameStats
from .driver import play_to_completion
from .random_bot import RandomBot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--max-turns', type=int, default=500)
    args = parser.parse_args()

    modes = {code: PowerMode.NEUTRAL for code in data.factions()}
    modes['NAA'] = PowerMode.BOT
    modes['AAC'] = PowerMode.BOT

    gs = build_game_state('starting_setup_200ipc', modes)
    stats = GameStats()
    engine = GameEngine(gs, stats=stats)
    seed_rng = random.Random(args.seed)
    bots = {
        'NAA': RandomBot(engine, 'NAA', rng=random.Random(seed_rng.random())),
        'AAC': RandomBot(engine, 'AAC', rng=random.Random(seed_rng.random())),
    }

    turns_played = play_to_completion(engine, bots, max_turns=args.max_turns)

    print(f'Played {turns_played} turns. Game over: {gs.game_over}')
    if not gs.game_over:
        print(f'(stopped at the {args.max_turns}-turn safety cap, not a decisive finish)')
    print()
    print(stats.report())


if __name__ == '__main__':
    main()
