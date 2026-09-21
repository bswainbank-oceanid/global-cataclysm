import random
import unittest

from server.lobby import build_session


def _by_type(messages, msg_type):
    return [m for m in messages if m['type'] == msg_type]


def _session(seed=1):
    """A human NAA against the bots UE and GPC (no alliances), fixed turn order."""
    def seat(mode, faction):
        return {'mode': mode, 'faction': faction, 'alliance': 0, 'strategy': 'independent', 'behavior': 'loyal'}
    seats = [seat('HUMAN', 'NAA'), seat('BOT', 'UE'), seat('BOT', 'GPC')] + [seat('NEUTRAL', f) for f in ('UER', 'PAF', 'AAC')]
    session, _ = build_session({'seats': seats, 'randomize_order': False, 'max_alliance_size': 1}, random.Random(seed))
    return session


def _advance_to(session, faction, phase, limit=80):
    messages = session.handle_message({'type': 'watch'})
    for _ in range(limit):
        queue = _by_type(messages, 'phase_queue')[0]
        if (queue['faction'], queue['phase']) == (faction, phase):
            return queue
        messages = session.handle_message({'type': 'next'})
        assert 'error' not in [m['type'] for m in messages], messages
    raise AssertionError(f'never reached {faction} {phase}')


def _hand_over(session, to, losers):
    """`to` takes every territory of `losers` but the first of each -- far more than double their income."""
    gs = session.engine.game_state
    for loser in losers:
        owned = sorted(tid for tid, t in gs.territories.items() if t.owner == loser
                       and session.engine.data.territories()[tid]['type'] == 'land')
        for tid in owned[1:]:
            gs.territories[tid].owner = to


class TestHumanSurrender(unittest.TestCase):
    def test_the_queue_lists_who_can_be_forced_to_surrender_and_why(self):
        session = _session()
        _advance_to(session, 'NAA', 'DIPLOMACY')
        _hand_over(session, 'NAA', ['UE'])
        session.stepper._plan_current_phase()
        block = session.stepper._queue['human']
        self.assertEqual([(t['target'], t['allied']) for t in block['surrender']], [('UE', False)])
        self.assertIn('income', block['surrender'][0]['reasons'])
        self.assertEqual(block['options']['eligible_invite_targets'], [])  # a maximum alliance size of 1

    def test_a_long_click_demand_eliminates_at_once_and_reports_its_events(self):
        session = _session()
        _advance_to(session, 'NAA', 'DIPLOMACY')
        _hand_over(session, 'NAA', ['UE'])
        reply = session.handle_message({'type': 'diplomacy_action', 'faction': 'NAA', 'action': 'surrender', 'target': 'UE'})
        self.assertEqual([m['type'] for m in reply], ['diplomacy_result', 'phase_queue', 'state'])
        self.assertEqual([e['kind'] for e in reply[0]['events']], ['surrender', 'faction_eliminated'])
        self.assertIn('income', reply[0]['events'][0]['reasons'])
        gs = session.engine.game_state
        self.assertTrue(gs.factions['UE'].eliminated)
        self.assertEqual(reply[1]['human']['surrender'], [])       # nobody left it may demand from
        self.assertEqual(reply[2]['game_state']['factions']['UE']['eliminated'], True)
        # UE is out of the turn order: after NAA it is GPC's turn
        nxt = _by_type(session.handle_message({'type': 'next'}), 'phase_queue')[0]
        self.assertEqual((nxt['faction'], nxt['phase']), ('GPC', 'START_OF_TURN'))

    def test_a_demand_can_be_made_before_or_after_the_alliance_action_and_more_than_once(self):
        session = _session()
        _advance_to(session, 'NAA', 'DIPLOMACY')
        _hand_over(session, 'NAA', ['UE'])
        for target in ('UE', 'GPC'):
            reply = session.handle_message({'type': 'diplomacy_action', 'faction': 'NAA', 'action': 'surrender', 'target': target})
            self.assertEqual(reply[0]['type'], 'error' if target == 'GPC' else 'diplomacy_result', target)  # no grounds against GPC

    def test_demanding_without_grounds_is_refused(self):
        session = _session()
        _advance_to(session, 'NAA', 'DIPLOMACY')
        reply = session.handle_message({'type': 'diplomacy_action', 'faction': 'NAA', 'action': 'surrender', 'target': 'UE'})
        self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'])
        self.assertFalse(session.engine.game_state.factions['UE'].eliminated)

    def test_forcing_the_last_faction_to_surrender_ends_the_game_when_the_phase_ends(self):
        session = _session()
        _advance_to(session, 'NAA', 'DIPLOMACY')
        _hand_over(session, 'NAA', ['UE', 'GPC'])
        for target in ('UE', 'GPC'):
            session.handle_message({'type': 'diplomacy_action', 'faction': 'NAA', 'action': 'surrender', 'target': target})
        self.assertTrue(session.stepper._queue['human']['game_would_end'])
        messages = session.handle_message({'type': 'next'})
        self.assertIn('game_over', [m['type'] for m in messages])


class TestBotWinsAlone(unittest.TestCase):
    def test_a_bot_that_can_force_everyone_to_surrender_queues_it_and_ends_the_game(self):
        session = _session()
        _advance_to(session, 'UE', 'DIPLOMACY')
        _hand_over(session, 'UE', ['NAA', 'GPC'])
        session.stepper._alliance_plan = None
        session.stepper._plan_current_phase()
        queue = session.stepper._queue
        kinds = [e['kind'] for e in queue['events']]
        self.assertEqual(kinds, ['alliance_plan', 'surrender_plan'])
        plan = queue['events'][1]
        self.assertTrue(plan['win'])
        self.assertEqual(sorted(plan['targets']), ['GPC', 'NAA'])
        messages = session.handle_message({'type': 'next'})
        self.assertIn('game_over', [m['type'] for m in messages])
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual([e['kind'] for e in result['events']].count('surrender'), 2)
        self.assertEqual(session.engine.game_state.active_factions(), ['UE'])


if __name__ == '__main__':
    unittest.main()
