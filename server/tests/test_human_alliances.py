import random
import unittest

from server.lobby import build_session
from server.session import GameSession  # noqa: F401  (build_session returns one)


def _by_type(messages, msg_type):
    return [m for m in messages if m['type'] == msg_type]


def _session(strategies, seed=1):
    """A human NAA, bots UE and GPC with the given alliance strategies, fixed turn order."""
    def seat(mode, faction, strategy='random'):
        return {'mode': mode, 'faction': faction, 'alliance': 0, 'strategy': strategy, 'behavior': 'loyal'}
    seats = [seat('HUMAN', 'NAA'), seat('BOT', 'UE', strategies[0]), seat('BOT', 'GPC', strategies[1])] + \
        [seat('NEUTRAL', f) for f in ('UER', 'PAF', 'AAC')]
    session, _ = build_session({'seats': seats, 'randomize_order': False}, random.Random(seed))
    return session


def _advance_to(session, faction, phase, limit=80):
    """Step the watch session until `faction`'s `phase` is the queued one; returns that queue."""
    messages = session.handle_message({'type': 'watch'})
    for _ in range(limit):
        queue = _by_type(messages, 'phase_queue')[0]
        if (queue['faction'], queue['phase']) == (faction, phase):
            return queue
        messages = session.handle_message({'type': 'next'})
        assert 'error' not in [m['type'] for m in messages], messages
    raise AssertionError(f'never reached {faction} {phase}')


def _find_invitation_of_the_human(strategies=('aggressive', 'aggressive')):
    """A session parked where a bot has queued an invitation to NAA."""
    for seed in range(1, 80):
        session = _session(strategies, seed=seed)
        messages = session.handle_message({'type': 'watch'})
        for _ in range(80):
            queue = _by_type(messages, 'phase_queue')[0]
            if queue['phase'] == 'ALLIANCES' and queue.get('invitation'):
                return session, queue
            messages = session.handle_message({'type': 'next'})
    raise AssertionError('no bot invited the human in 80 seeds')


class TestHumanAlliancePhase(unittest.TestCase):
    def test_the_queue_carries_the_options(self):
        session = _session(('independent', 'independent'))
        queue = _advance_to(session, 'NAA', 'ALLIANCES')
        human = queue['human']
        self.assertEqual(human['kind'], 'alliance')
        self.assertEqual(human['members'], ['NAA'])
        self.assertEqual(human['options']['eligible_invite_targets'], ['GPC', 'UE'])
        self.assertFalse(human['options']['can_withdraw'])
        self.assertEqual(human['staged'], {'action': 'none'})
        self.assertFalse(human['game_would_end'])
        self.assertEqual(queue['events'][0]['action'], 'none')

    def test_staging_an_invite_shows_it_without_revealing_the_bots_answer(self):
        session = _session(('aggressive', 'independent'))
        _advance_to(session, 'NAA', 'ALLIANCES')
        reply = session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': 'invite', 'target': 'UE'})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        event = reply[0]['events'][0]
        self.assertEqual((event['action'], event['target']), ('invite', 'UE'))
        self.assertIsNone(event.get('accepts'))
        self.assertEqual(reply[0]['human']['staged'], {'action': 'invite', 'target': 'UE'})

    def test_an_invited_bot_decides_by_its_own_strategy_when_the_phase_executes(self):
        for strategy, joins in (('aggressive', True), ('independent', False)):
            session = _session((strategy, 'independent'))
            _advance_to(session, 'NAA', 'ALLIANCES')
            session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': 'invite', 'target': 'UE'})
            result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
            kinds = [e['kind'] for e in result['events']]
            self.assertEqual('alliance_joined' in kinds, joins, strategy)
            self.assertEqual('alliance_declined' in kinds, not joins, strategy)
            self.assertEqual(session.engine.game_state.factions['NAA'].alliance is not None, joins)

    def test_illegal_choices_are_rejected_with_the_queue_unchanged(self):
        session = _session(('independent', 'independent'))
        _advance_to(session, 'NAA', 'ALLIANCES')
        for action, target in (('withdraw', None), ('invite', 'NAA'), ('invite', 'PAF'), ('invite', None), ('teleport', None)):
            reply = session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': action, 'target': target})
            self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'], (action, target))
            self.assertEqual(reply[1]['human']['staged'], {'action': 'none'})

    def test_only_the_active_human_in_its_alliances_phase_may_stage(self):
        session = _session(('independent', 'independent'))
        session.handle_message({'type': 'watch'})  # NAA's Purchase phase
        self.assertEqual(session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': 'none'})[0]['type'], 'error')
        self.assertEqual(session.handle_message({'type': 'stage_alliance', 'faction': 'UE', 'action': 'none'})[0]['type'], 'error')

    def test_a_human_can_withdraw_once_allied_and_it_takes_effect(self):
        session = _session(('aggressive', 'independent'))
        _advance_to(session, 'NAA', 'ALLIANCES')
        session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': 'invite', 'target': 'UE'})
        session.handle_message({'type': 'next'})
        gs = session.engine.game_state
        self.assertIsNotNone(gs.factions['NAA'].alliance)
        # the next time round: allied, so withdrawing is offered
        messages = session.handle_message({'type': 'watch'})
        queue = None
        for _ in range(80):
            queue = _by_type(messages, 'phase_queue')[0]
            if (queue['faction'], queue['phase']) == ('NAA', 'ALLIANCES'):
                break
            messages = session.handle_message({'type': 'next'})
        self.assertEqual(queue['human']['members'], ['NAA', 'UE'])
        self.assertTrue(queue['human']['options']['can_withdraw'])
        reply = session.handle_message({'type': 'stage_alliance', 'faction': 'NAA', 'action': 'withdraw'})
        self.assertEqual(reply[0]['human']['staged'], {'action': 'withdraw'})
        result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
        self.assertIn('alliance_withdrawal', [e['kind'] for e in result['events']])
        self.assertIsNone(gs.factions['NAA'].alliance)


class TestABotInvitingTheHuman(unittest.TestCase):
    def test_it_waits_for_their_answer_and_then_uses_it(self):
        session, queue = _find_invitation_of_the_human()
        inv = queue['invitation']
        self.assertEqual(inv['to'], 'NAA')
        self.assertFalse(inv['answered'])
        self.assertIn(inv['from'], inv['members'])
        self.assertIsNone(queue['events'][0]['accepts'])
        # the phase can't be executed until the human answers
        reply = session.handle_message({'type': 'next'})
        self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'])
        # only the invited human can answer
        self.assertEqual(session.handle_message({'type': 'respond_invitation', 'faction': 'GPC', 'accept': True})[0]['type'], 'error')
        answered = session.handle_message({'type': 'respond_invitation', 'faction': 'NAA', 'accept': True})
        self.assertEqual([m['type'] for m in answered], ['phase_queue'])
        self.assertTrue(answered[0]['invitation']['answered'])
        self.assertTrue(answered[0]['events'][0]['accepts'])
        self.assertEqual(answered[0]['events'][0]['target'], 'NAA')  # the re-queue kept the same plan
        result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
        self.assertIn('alliance_joined', [e['kind'] for e in result['events']])
        gs = session.engine.game_state
        self.assertIsNotNone(gs.factions['NAA'].alliance)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions[inv['from']].alliance)

    def test_declining_leaves_no_alliance(self):
        session, _ = _find_invitation_of_the_human()
        session.handle_message({'type': 'respond_invitation', 'faction': 'NAA', 'accept': False})
        result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
        self.assertIn('alliance_declined', [e['kind'] for e in result['events']])
        self.assertIsNone(session.engine.game_state.factions['NAA'].alliance)

    def test_answering_with_nothing_pending_is_an_error(self):
        session = _session(('independent', 'independent'))
        session.handle_message({'type': 'watch'})
        self.assertEqual(session.handle_message({'type': 'respond_invitation', 'faction': 'NAA', 'accept': True})[0]['type'], 'error')


if __name__ == '__main__':
    unittest.main()
