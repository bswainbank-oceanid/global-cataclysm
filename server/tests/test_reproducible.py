"""A seeded game must replay exactly -- setup, bots and dice -- whatever Python's hash seed is.

The hash seed changes the iteration order of every set of strings, so this catches decisions
that depend on it; the rest is the dice (the lobby used to leave them unseeded)."""
import hashlib
import json
import os
import random
import subprocess
import sys
import textwrap
import unittest

from engine.bots.driver import play_to_completion
from server.lobby import build_session

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PLAY = textwrap.dedent('''
    import hashlib, json, random, sys
    sys.path.insert(0, {root!r})
    from engine.bots.driver import play_to_completion
    from server.lobby import build_session
    seat = {{'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': {ai!r}}}
    session, _ = build_session({{'seats': [dict(seat) for _ in range(6)], 'seed': {seed}, 'dev': {{'bot_budget': 250}}}})
    play_to_completion(session.engine, session.bots, max_turns={turns})
    log = '\\n'.join(json.dumps(e, sort_keys=True) for e in session.turn_log.events)
    print(hashlib.sha1(log.encode()).hexdigest())
''')


def play_in_a_fresh_interpreter(seed, hash_seed, ai='random', turns=40):
    code = PLAY.format(root=ROOT, seed=seed, turns=turns, ai=ai)
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
    out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def digest(seed, ai='random', turns=40):
    seat = {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': ai}
    session, _ = build_session({'seats': [dict(seat) for _ in range(6)], 'seed': seed, 'dev': {'bot_budget': 250}})
    play_to_completion(session.engine, session.bots, max_turns=turns)
    log = '\n'.join(json.dumps(e, sort_keys=True) for e in session.turn_log.events)
    return hashlib.sha1(log.encode()).hexdigest()


class TestSeededGamesReplayExactly(unittest.TestCase):
    def test_the_same_seed_gives_the_same_game(self):
        self.assertEqual(digest(11), digest(11))
        self.assertEqual(digest(11, 'strategy', 25), digest(11, 'strategy', 25))

    def test_different_seeds_give_different_games(self):
        self.assertNotEqual(digest(11), digest(12))

    def test_the_game_does_not_depend_on_pythons_hash_order(self):
        for seed in (3, 8):
            digests = {play_in_a_fresh_interpreter(seed, h) for h in (0, 1, 97)}
            self.assertEqual(len(digests), 1, f'seed {seed}: the log changed with PYTHONHASHSEED')

    def test_the_strategy_bots_do_not_depend_on_hash_order_or_the_clock_either(self):
        digests = {play_in_a_fresh_interpreter(5, h, 'strategy', 20) for h in (0, 3)}
        self.assertEqual(len(digests), 1, 'the strategy bots planning must be budgeted in work, not seconds')

    def test_an_unseeded_game_is_still_random(self):
        seat = {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': 'random'}
        firsts = set()
        for _ in range(6):
            session, _ = build_session({'seats': [dict(seat) for _ in range(6)]})
            firsts.add(session.engine.game_state.active_faction)
        self.assertGreater(len(firsts), 1)


if __name__ == '__main__':
    unittest.main()
