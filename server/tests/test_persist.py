"""Saving and reloading a live game (server/persist.py): a game reloaded at any point -- from its turn-start
snapshot plus the decisions since -- must carry on exactly as if it had never stopped."""
import hashlib
import json
import unittest

from server import persist
from server.lobby import build_session


def bot(ai='random', faction='random', alliance=0):
    return {'mode': 'BOT', 'faction': faction, 'alliance': alliance, 'strategy': 'random', 'behavior': 'random', 'ai': ai}


def seat(mode, faction='random'):
    return {'mode': mode, 'faction': faction, 'alliance': 0, 'strategy': 'random', 'behavior': 'random'}


def start(settings):
    session, seats = build_session(settings)
    game = persist.RecordedGame(session, persist.setup_for(session, seats, settings))
    game.handle_message({'type': 'watch'})
    return game


def reload(game):
    """What a restart does: the save goes through JSON (the database), and the game is loaded back."""
    return persist.load(json.loads(json.dumps(game.doc())))


def decide(game, step, surrender_at=None):
    """The next message a scripted player sends: a human buys one Infantry on each of its Purchase phases
    (then confirms), may surrender at `surrender_at`; everything else is just "next"."""
    queue = game.stepper._queue
    gs = game.engine.game_state
    human = next((c for c, f in gs.factions.items() if f.mode.name == 'HUMAN'), None)
    if step == surrender_at and human and human in gs.active_factions():
        return {'type': 'surrender', 'faction': human}
    if queue and queue.get('phase') == 'PURCHASE' and queue.get('human') and not queue['human']['orders']:
        land = [tid for tid, t in sorted(queue['human']['targets'].items())
                if t['remaining'] > 0 and game.engine.data.territories()[tid]['type'] == 'land']
        if land:
            return {'type': 'stage_purchase', 'faction': queue['faction'],
                    'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': land[0]}]}
    return {'type': 'next'}


def fingerprint(game):
    """Everything that would differ if the reloaded game had drifted: the board, every event, the dice and
    every bot's generator, the stats."""
    engine = game.engine
    parts = [engine.game_state.to_dict(), game.turn_log.events, persist._rng_state(engine._combat_rng),
             {f: persist._rng_state(b.rng) for f, b in sorted(game.bots.items())}, persist._stats_doc(engine.stats)]
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def play(settings, steps, reload_at=(), surrender_at=None):
    game = start(settings)
    for step in range(steps):
        if step in reload_at:
            game = reload(game)
        if game.engine.game_state.game_over:
            break
        game.handle_message(decide(game, step, surrender_at))
    return game


class TestReloadingChangesNothing(unittest.TestCase):
    def check(self, settings, steps, reload_at, surrender_at=None):
        straight = play(settings, steps, surrender_at=surrender_at)
        reloaded = play(settings, steps, reload_at, surrender_at=surrender_at)
        self.assertEqual(fingerprint(reloaded), fingerprint(straight))
        self.assertGreater(straight.snapshots_taken, 3, 'the run should cross several turn starts')
        return straight

    def test_random_bots_reloaded_every_few_steps(self):
        self.check({'seats': [bot() for _ in range(6)], 'seed': 21}, 160, reload_at=range(1, 160, 7))

    def test_strategy_bots_reloaded_mid_turn(self):
        # (a strategy bot plans its whole turn at Purchase and uses the plan until Non-Combat Move:
        # reloading in between must plan it again, identically)
        settings = {'seats': [bot('strategy'), bot('strategy'), bot('random'), seat('NEUTRAL'), seat('NONCOMBATANT'),
                              seat('NONCOMBATANT')], 'seed': 22, 'allow_combat_first_turn': True}
        self.check(settings, 60, reload_at=[3, 9, 10, 17, 26, 33, 41, 52])

    def test_a_human_seats_decisions_replay(self):
        settings = {'seats': [seat('HUMAN'), bot(), bot(), bot(), seat('NONCOMBATANT'), seat('NONCOMBATANT')], 'seed': 23}
        game = self.check(settings, 120, reload_at=range(2, 120, 5))
        bought = [e for e in game.turn_log.events if e['kind'] == 'purchase' and e['orders']
                  and game.engine.game_state.factions[e['faction']].mode.name == 'HUMAN']
        self.assertTrue(bought, 'the human should have bought something')

    def test_an_out_of_turn_surrender_replays(self):
        settings = {'seats': [seat('HUMAN'), bot(), bot(), seat('NONCOMBATANT'), seat('NONCOMBATANT'),
                              seat('NONCOMBATANT')], 'seed': 24}
        game = self.check(settings, 90, reload_at=[44, 47, 60], surrender_at=45)
        human = next(c for c, f in game.engine.game_state.factions.items() if f.mode.name == 'HUMAN')
        self.assertTrue(game.engine.game_state.factions[human].eliminated)

    def test_a_new_scenarios_generated_map_is_saved_with_the_game(self):
        settings = {'scenario': {'kind': 'new'}, 'seats': [bot(), bot(), bot(), seat('NOT_PLAYING'), seat('NOT_PLAYING'),
                                                           seat('NOT_PLAYING')], 'seed': 25}
        self.check(settings, 60, reload_at=range(5, 60, 9))
        game = start(settings)
        self.assertTrue(game.doc()['setup']['generated'])
        self.assertIsNotNone(game.doc()['setup']['scenario_id'])


class TestTheSave(unittest.TestCase):
    def test_a_snapshot_is_taken_at_each_turn_start_and_the_decisions_restart_from_it(self):
        game = start({'seats': [bot() for _ in range(3)] + [seat('NONCOMBATANT')] * 3, 'seed': 26})
        self.assertEqual(game.decisions, [])  # (the watch that queued the first Start of Turn began a fresh save)
        game.handle_message({'type': 'next'})  # Start of Turn -> Purchase
        game.handle_message({'type': 'next'})
        self.assertEqual(len(game.decisions), 2)
        taken = game.snapshots_taken
        while game.snapshots_taken == taken:
            game.handle_message({'type': 'next'})
        self.assertEqual(game.decisions, [])
        self.assertEqual(game.stepper._queue['phase'], 'START_OF_TURN')

    def test_joins_are_not_recorded(self):
        game = start({'seats': [seat('HUMAN')] + [bot()] * 2 + [seat('NONCOMBATANT')] * 3, 'seed': 27})
        human = next(c for c, f in game.engine.game_state.factions.items() if f.mode.name == 'HUMAN')
        game.handle_message({'type': 'join', 'faction': human})
        self.assertEqual(game.decisions, [])

    def test_a_save_is_plain_json(self):
        game = play({'seats': [bot() for _ in range(6)], 'seed': 28}, 25)
        doc = game.doc()
        self.assertEqual(json.loads(json.dumps(doc)), doc)
        self.assertEqual(doc['version'], persist.VERSION)

    def test_a_save_from_another_version_is_refused(self):
        game = start({'seats': [bot() for _ in range(6)], 'seed': 29})
        doc = dict(game.doc(), version=persist.VERSION + 1)
        with self.assertRaises(ValueError):
            persist.load(doc)


if __name__ == '__main__':
    unittest.main()
