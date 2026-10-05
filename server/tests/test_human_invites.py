"""A human inviting another human to an alliance: the invited faction is asked -- even when the same
player controls both -- and the game waits for its answer (server/stepper.py, server/session.py)."""
import json
import unittest
from unittest import mock

from server import accounts, games, persist
from server.hub import Hub
from server.lobby import build_session
from server.store import Store
from server.tests.test_persist import bot, fingerprint, seat

TWO_HUMANS = {'seats': [seat('HUMAN'), seat('HUMAN'), bot(), seat('NONCOMBATANT'), seat('NONCOMBATANT'),
                        seat('NONCOMBATANT')], 'max_alliance_size': 2, 'armistice_rounds': 0}


def auto_game(seed):
    settings = dict(TWO_HUMANS, seed=seed)
    session, seats = build_session(settings, auto=True, max_humans=None)
    return persist.RecordedGame(session, persist.setup_for(session, seats, settings))


def humans(game):
    return [c for c, f in game.engine.game_state.factions.items() if f.mode.name == 'HUMAN']


def to_diplomacy(game, limit=200):
    """Plays (humans just ending their phases) until a human's Diplomacy can invite the other human;
    returns (inviter, target)."""
    for _ in range(limit):
        while game.step() is not None:
            pass
        [who] = game.waiting_for()
        queue = game.stepper._queue
        if queue['phase'] == 'DIPLOMACY':
            options = game.engine.legal_alliance_options(who)['eligible_invite_targets']
            other = next(h for h in humans(game) if h != who)
            if other in options:
                return who, other
        game.handle_message({'type': 'end_phase', 'faction': who})
    raise AssertionError('no Diplomacy phase with a human to invite')


def invite(game, inviter, target):
    return game.handle_message({'type': 'diplomacy_action', 'faction': inviter, 'action': 'invite', 'target': target})


class TestAskingTheOtherHuman(unittest.TestCase):
    def test_the_invited_human_is_asked_and_the_game_waits(self):
        game = auto_game(71)
        inviter, target = to_diplomacy(game)
        out = invite(game, inviter, target)
        result = next(m for m in out if m['type'] == 'diplomacy_result')
        self.assertEqual(result['events'], [{'kind': 'alliance_invite_sent', 'faction': inviter, 'target': target}])
        self.assertEqual(game.waiting_for(), [target])
        queue = game.stepper._queue
        self.assertEqual((queue['invitation']['from'], queue['invitation']['to'], queue['invitation']['by_human']),
                         (inviter, target, True))
        self.assertEqual(out[-1], dict(out[-1], type='waiting', **{'for': [target]}))
        self.assertNotIn(target, game.engine.alliance_members(inviter))  # nothing decided yet

    def test_nothing_else_happens_until_it_is_answered(self):
        game = auto_game(72)
        inviter, target = to_diplomacy(game)
        invite(game, inviter, target)
        self.assertIsNone(game.step())
        [error] = game.handle_message({'type': 'end_phase', 'faction': inviter})
        self.assertEqual(error['type'], 'error')
        out = game.handle_message({'type': 'diplomacy_action', 'faction': inviter, 'action': 'withdraw'})
        self.assertEqual(out[0]['message'], f"{target} has not answered {inviter}'s invitation yet")
        [error] = game.handle_message({'type': 'respond_invitation', 'faction': inviter, 'accept': True})
        self.assertEqual(error['message'], 'no invitation is waiting for your answer')

    def test_accepting_forms_the_alliance_and_the_inviter_carries_on(self):
        game = auto_game(73)
        inviter, target = to_diplomacy(game)
        invite(game, inviter, target)
        out = game.handle_message({'type': 'respond_invitation', 'faction': target, 'accept': True})
        result = next(m for m in out if m['type'] == 'diplomacy_result')
        self.assertEqual((result['faction'], result['answered_by'], result['accepted']), (inviter, target, True))
        self.assertIn('alliance_joined', [e['kind'] for e in result['events']])
        self.assertIn(target, game.engine.alliance_members(inviter))
        self.assertEqual(game.waiting_for(), [inviter])
        self.assertNotIn('invitation', game.stepper._queue)
        self.assertEqual(game.handle_message({'type': 'end_phase', 'faction': inviter})[0]['type'], 'phase_result')

    def test_declining_leaves_them_apart_and_uses_the_inviters_alliance_action(self):
        game = auto_game(74)
        inviter, target = to_diplomacy(game)
        invite(game, inviter, target)
        out = game.handle_message({'type': 'respond_invitation', 'faction': target, 'accept': False})
        result = next(m for m in out if m['type'] == 'diplomacy_result')
        self.assertEqual(result['accepted'], False)
        self.assertIn('alliance_declined', [e['kind'] for e in result['events']])
        self.assertNotIn(target, game.engine.alliance_members(inviter))
        self.assertEqual(game.waiting_for(), [inviter])
        out = invite(game, inviter, target)  # the phase's one alliance action is spent
        self.assertEqual(out[0]['type'], 'error')
        self.assertTrue(game.stepper._queue['human']['options']['alliance_action_used'])

    def test_the_invitation_lapses_if_the_invited_faction_leaves(self):
        game = auto_game(75)
        inviter, target = to_diplomacy(game)
        invite(game, inviter, target)
        game.handle_message({'type': 'surrender', 'faction': target})
        self.assertEqual(game.waiting_for(), [inviter])
        self.assertIsNone(game.stepper._invitation)

    def test_a_reload_while_it_waits_brings_the_question_back(self):
        game = auto_game(76)
        inviter, target = to_diplomacy(game)
        invite(game, inviter, target)
        again = persist.load(json.loads(json.dumps(game.doc())))
        self.assertEqual(again.waiting_for(), [target])
        for g in (game, again):
            g.handle_message({'type': 'respond_invitation', 'faction': target, 'accept': True})
        self.assertEqual(fingerprint(again), fingerprint(game))


class TestOnePlayerBothFactions(unittest.TestCase):
    def test_the_player_is_asked_for_the_faction_invited(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        store = Store(':memory:')
        self.addCleanup(store.close)
        hub = Hub(store)
        hub.connect('ann')
        [(_, msg)] = hub.handle('ann', {'type': 'register', 'player_name': 'Ann', 'email': 'a@x.com', 'password': 'pw'})
        ann = msg['user']['id']
        record = games.create(store, ann, {'kind': 'fixed', 'id': 'fixed'}, dict(TWO_HUMANS, seed=77))
        games.take_seat(store, record['id'], ann, 1)
        games.take_seat(store, record['id'], ann, 2)
        games.launch(store, record['id'], ann)
        hub.start_game(record['id'])
        hub.handle('ann', {'type': 'enter_game', 'game_id': record['id']})
        game = hub.sessions[record['id']]
        inviter, target = to_diplomacy(game)
        out = hub.handle('ann', {'type': 'diplomacy_action', 'faction': inviter, 'action': 'invite', 'target': target})
        waiting = [m for keys, m in out if m['type'] == 'waiting']
        self.assertEqual(waiting[0]['for'], [target])  # asked, though Ann plays both
        out = hub.handle('ann', {'type': 'respond_invitation', 'faction': target, 'accept': True})
        self.assertIn(target, game.engine.alliance_members(inviter))
        self.assertEqual(games.get(store, record['id'])['progress']['waiting_for'], [ann])


if __name__ == '__main__':
    unittest.main()
