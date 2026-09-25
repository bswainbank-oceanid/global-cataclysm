"""
Runs a full NAA-vs-AAC bot game (the other four factions left NEUTRAL) to
completion, then prints a stats.GameStats report -- territory captures in
turn order, and per-faction/per-unit-type deployed/promotions/deaths/
kills (with transport-form deaths called out). A manual demo/verification
script, not part of the test suite (test_random_bot.py's
TestPlayToCompletion exercises the same path with a small turn cap, for
speed).

Run: python -m engine.bots.run_game [--seed N] [--max-turns N]
    [--no-randomize-play-order] [--allow-combat-moves-first-turn]
    [--no-noncombat-moves-first-turn]
"""
import argparse
import random

from .. import data
from ..engine import GameEngine
from ..setup import build_game_state
from ..state import FactionMode
from ..stats import GameStats
from .driver import play_to_completion
from .random_bot import RandomBot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--max-turns', type=int, default=500)
    # game_start_settings (data/rules.json) -- randomize_play_order and
    # allow_noncombat_moves_first_turn default on, allow_combat_moves_
    # first_turn defaults off, matching engine.setup.build_game_state's
    # own defaults.
    parser.add_argument('--no-randomize-play-order', dest='randomize_play_order', action='store_false')
    parser.add_argument('--allow-combat-moves-first-turn', action='store_true')
    parser.add_argument('--no-noncombat-moves-first-turn', dest='allow_noncombat_moves_first_turn', action='store_false')
    args = parser.parse_args()

    codes = list(data.factions())
    pair = ('NAA', 'AAC') if {'NAA', 'AAC'} <= set(codes) else tuple(codes[:2])
    modes = {code: FactionMode.NEUTRAL for code in codes}
    for code in pair:
        modes[code] = FactionMode.BOT

    gs = build_game_state(
        modes,
        randomize_play_order=args.randomize_play_order,
        allow_combat_moves_first_turn=args.allow_combat_moves_first_turn,
        allow_noncombat_moves_first_turn=args.allow_noncombat_moves_first_turn,
        rng=random.Random(args.seed) if args.seed is not None else None,
    )
    stats = GameStats()
    seed_rng = random.Random(args.seed)
    # combat_rng: resolve_combat's dice source for the whole game -- must
    # be explicitly seeded here (from the SAME seed_rng chain as the
    # bots below) or combat outcomes are drawn from OS entropy and
    # --seed doesn't actually make the game reproducible (a real bug
    # this session -- see GameEngine.__init__'s own docstring comment).
    engine = GameEngine(gs, stats=stats, combat_rng=random.Random(seed_rng.random()))
    bots = {code: RandomBot(engine, code, rng=random.Random(seed_rng.random())) for code in pair}

    turns_played = play_to_completion(engine, bots, max_turns=args.max_turns)

    print(f'Played {turns_played} turns. Game over: {gs.game_over}')
    if not gs.game_over:
        print(f'(stopped at the {args.max_turns}-turn safety cap, not a decisive finish)')
    print()
    print(stats.report(game_state=gs))


if __name__ == '__main__':
    main()
