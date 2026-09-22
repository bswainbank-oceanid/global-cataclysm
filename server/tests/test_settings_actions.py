"""The Settings actions -- "surrender" (self) and "propose_armistice"/"respond_armistice" -- both
out-of-band, handled directly by GameSession, not tied to any queued phase."""
import random
import unittest

from engine import data as data_module
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode
from engine.stats import GameStats
from engine.turn_log import TurnLog
from server.lobby import build_session
from server.session import GameSession


def _by_type(messages, msg_type):
    return [m for m in messages if m['type'] == msg_type]


def _session(humans=('NAA',), bots=('UE', 'GPC'), seed=1):
    def seat(mode, faction):
        return {'mode': mode, 'faction': faction, 'alliance': 0, 'strategy': 'independent', 'behavior': 'loyal'}
    seated = {f for f in humans} | {f for f in bots}
    seats = [seat('HUMAN', f) for f in humans] + [seat('BOT', f) for f in bots] + \
        [seat('NEUTRAL', f) for f in ('NAA', 'UE', 'GPC', 'AAC', 'PAF', 'UER') if f not in seated]
    session, _ = build_session({'seats': seats, 'randomize_order': False, 'max_alliance_size': 1}, random.Random(seed))
    return session


def _multi_human_session(humans, bots=(), seed=1):
    """Bypasses the lobby's one-human-seat cap (server/lobby.py's own rule, not an engine one -- the
    engine/protocol already support more, e.g. alliance_invite/alliance_invite_response) to exercise the
    human-to-human armistice path: asking a SECOND human and waiting on THEIR answer."""
    modes = {f: FactionMode.NEUTRAL for f in data_module.factions()}
    for f in humans:
        modes[f] = FactionMode.HUMAN
    for f in bots:
        modes[f] = FactionMode.BOT
    rng = random.Random(seed)
    gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False, max_alliance_size=1, rng=rng)
    turn_log = TurnLog()
    engine = GameEngine(gs, data_module, turn_log=turn_log, combat_rng=random.Random(rng.random()), stats=GameStats())
    bot_objs = {f: RandomBot(engine, f, rng=random.Random(rng.random())) for f in bots}
    return GameSession(engine, turn_log, bot_objs)


class TestSelfSurrender(unittest.TestCase):
    def test_it_works_from_anywhere_not_just_your_own_phase(self):
        session = _session()
        session.handle_message({'type': 'watch'})  # NAA's own Purchase phase is queued...
        gs = session.engine.game_state
        session.handle_message({'type': 'next'})  # ...but nobody has to be ON it: advance past it first
        reply = session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        self.assertEqual([m['type'] for m in reply], ['self_surrender_result', 'state'])
        self.assertEqual([e['kind'] for e in reply[0]['events']], ['self_surrender', 'faction_eliminated'])
        self.assertTrue(gs.factions['NAA'].eliminated)
        self.assertFalse(gs.game_over)  # UE and GPC are both still around, unallied

    def test_the_game_ends_at_once_if_it_was_the_last_one_standing(self):
        session = _session(humans=('NAA',), bots=('UE',))
        reply = session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        self.assertIn('game_over', [m['type'] for m in reply])
        self.assertEqual(reply[-1]['type'], 'game_over')
        self.assertTrue(reply[-1]['report'])

    def test_a_stale_queue_does_not_wedge_the_game(self):
        # NAA's own Purchase phase is the one currently queued when NAA surrenders mid-that: the queue
        # (rebuilt fresh) still names NAA -- GameState.active_faction only moves at advance_turn() -- but
        # with nothing left to decide (see _commit's active guards), and "next" walks straight past it to
        # a faction that is still actually playing.
        session = _session()
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        queue = _by_type(session.handle_message({'type': 'watch'}), 'phase_queue')[0]
        self.assertEqual(queue['events'], [])
        seen = set()
        for _ in range(30):
            messages = session.handle_message({'type': 'next'})
            self.assertNotIn('error', [m['type'] for m in messages])
            for q in _by_type(messages, 'phase_queue'):
                seen.add(q['faction'])
            if 'UE' in seen or 'GPC' in seen:
                break
        self.assertTrue({'UE', 'GPC'} & seen)

    def test_cannot_surrender_twice(self):
        session = _session(humans=('NAA',), bots=('UE', 'GPC'))
        session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        reply = session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        self.assertEqual(reply[0]['type'], 'error')

    def test_cannot_surrender_a_non_human_faction_or_an_unknown_one(self):
        session = _session()
        self.assertEqual(session.handle_message({'type': 'surrender', 'faction': 'UE'})[0]['type'], 'error')
        self.assertEqual(session.handle_message({'type': 'surrender', 'faction': 'ZZZ'})[0]['type'], 'error')

    def test_next_still_works_normally_after_a_surrender_that_does_not_end_the_game(self):
        session = _session(humans=('NAA',), bots=('UE', 'GPC'))
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'surrender', 'faction': 'NAA'})
        reply = session.handle_message({'type': 'next'})
        self.assertNotIn('error', [m['type'] for m in reply])


class TestProposeArmistice(unittest.TestCase):
    def test_bots_accept_at_once_and_the_game_ends(self):
        session = _session(humans=('NAA',), bots=('UE', 'GPC'))
        reply = session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        self.assertIn('game_over', [m['type'] for m in reply])
        resolved = _by_type(reply, 'armistice_resolved')[0]
        self.assertTrue(resolved['accepted'])
        self.assertIsNone(resolved['declined_by'])
        report = _by_type(reply, 'game_over')[0]['report']
        by_fac = {r['faction']: r for r in report}
        self.assertEqual(by_fac['NAA']['victory_status'], 'Armistice')
        self.assertEqual(by_fac['UE']['victory_status'], 'Armistice')
        self.assertEqual(by_fac['GPC']['victory_status'], 'Armistice')

    def test_a_second_human_is_asked_and_next_is_refused_until_they_answer(self):
        session = _multi_human_session(humans=('NAA', 'UE'), bots=('GPC',))
        reply = session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        self.assertEqual([m['type'] for m in reply], ['armistice_proposed'])
        self.assertEqual(reply[0]['awaiting'], ['UE'])
        blocked = session.handle_message({'type': 'next'})
        self.assertEqual(blocked[0]['type'], 'error')

    def test_the_asked_human_accepting_ends_the_game(self):
        session = _multi_human_session(humans=('NAA', 'UE'), bots=('GPC',))
        session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        reply = session.handle_message({'type': 'respond_armistice', 'faction': 'UE', 'accept': True})
        self.assertIn('game_over', [m['type'] for m in reply])
        self.assertTrue(_by_type(reply, 'armistice_resolved')[0]['accepted'])

    def test_the_asked_human_declining_ends_the_proposal_not_the_game(self):
        session = _multi_human_session(humans=('NAA', 'UE'), bots=('GPC',))
        session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        reply = session.handle_message({'type': 'respond_armistice', 'faction': 'UE', 'accept': False})
        self.assertNotIn('game_over', [m['type'] for m in reply])
        resolved = _by_type(reply, 'armistice_resolved')[0]
        self.assertFalse(resolved['accepted'])
        self.assertEqual(resolved['declined_by'], 'UE')
        self.assertFalse(session.engine.game_state.game_over)
        # and next() works again immediately
        self.assertNotIn('error', [m['type'] for m in session.handle_message({'type': 'next'})])

    def test_only_one_proposal_may_be_pending_at_a_time(self):
        session = _multi_human_session(humans=('NAA', 'UE'), bots=('GPC',))
        session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        reply = session.handle_message({'type': 'propose_armistice', 'faction': 'UE'})
        self.assertEqual(reply[0]['type'], 'error')

    def test_answering_with_nothing_pending_is_an_error(self):
        session = _session()
        reply = session.handle_message({'type': 'respond_armistice', 'faction': 'NAA', 'accept': True})
        self.assertEqual(reply[0]['type'], 'error')

    def test_an_already_eliminated_human_may_still_propose(self):
        session = _multi_human_session(humans=('NAA', 'UE'), bots=('GPC',))
        session.engine.surrender('NAA')
        self.assertTrue(session.engine.game_state.factions['NAA'].eliminated)
        self.assertFalse(session.engine.game_state.game_over)
        reply = session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        self.assertEqual([m['type'] for m in reply], ['armistice_proposed'])
        self.assertEqual(reply[0]['awaiting'], ['UE'])  # GPC is a bot, not asked; NAA itself isn't asked either

    def test_a_bot_cannot_propose_and_neither_can_an_unknown_faction(self):
        session = _session(humans=('NAA',), bots=('UE', 'GPC'))
        self.assertEqual(session.handle_message({'type': 'propose_armistice', 'faction': 'UE'})[0]['type'], 'error')
        self.assertEqual(session.handle_message({'type': 'propose_armistice', 'faction': 'ZZZ'})[0]['type'], 'error')

    def test_cannot_propose_once_the_game_is_over(self):
        session = _session(humans=('NAA',), bots=('UE',))
        session.engine.surrender('NAA')
        self.assertTrue(session.engine.game_state.game_over)
        reply = session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        self.assertEqual(reply[0]['type'], 'error')

    def test_three_humans_all_must_agree(self):
        session = _multi_human_session(humans=('NAA', 'UE', 'GPC'), bots=())
        session.handle_message({'type': 'propose_armistice', 'faction': 'NAA'})
        r1 = session.handle_message({'type': 'respond_armistice', 'faction': 'UE', 'accept': True})
        self.assertNotIn('game_over', [m['type'] for m in r1])
        self.assertEqual(_by_type(r1, 'armistice_proposed')[0]['awaiting'], ['GPC'])
        r2 = session.handle_message({'type': 'respond_armistice', 'faction': 'GPC', 'accept': True})
        self.assertIn('game_over', [m['type'] for m in r2])


if __name__ == '__main__':
    unittest.main()
