"""A human player who surrenders: on their own turn, the turn ends there and the next faction's begins (nothing more
of theirs is played); and when they were the last human player in the game, the game holds still while they choose
-- "choose_end": end it here (as an armistice among those still in it: the faction ahead wins) or let the bots play
it out, which they then watch as a spectator."""
import unittest

from engine.state import FactionMode
from server import games
from server.lobby import build_session
from server.tests.test_hub import settings
from server.tests.test_persist import bot, reload, seat, start
from server.tests.test_seat_to_bot import SeatTest

OFF = seat('NONCOMBATANT')


def game(*seats, seed=5):
    session, _ = build_session({'seats': list(seats) + [OFF] * (6 - len(seats)), 'seed': seed, 'armistice_rounds': 0}, max_humans=6)
    session.handle_message({'type': 'watch'})
    return session


def humans(session):
    return [c for c, f in session.engine.game_state.factions.items() if f.mode == FactionMode.HUMAN]


def to_own_combat_move(session, faction):
    """Steps on (buying nothing) until `faction`'s Combat Move awaits it."""
    gs = session.engine.game_state
    for _ in range(500):
        if gs.active_faction == faction and session.waiting_for() == [faction]:
            if gs.phase.name == 'COMBAT_MOVE':
                return
            if gs.phase.name == 'PURCHASE':
                session.handle_message({'type': 'purchase', 'faction': faction, 'orders': []})
                continue
        session.handle_message({'type': 'next'})
    raise AssertionError('never reached its Combat Move')


class TestSurrenderingOnYourOwnTurn(unittest.TestCase):
    def test_the_turn_ends_there_and_the_next_in_order_begins(self):
        session = game(seat('HUMAN'), bot(), bot())
        [human] = humans(session)
        to_own_combat_move(session, human)
        gs = session.engine.game_state
        order = [c for c in gs.factions if c in gs.active_factions()]
        following = order[(order.index(human) + 1) % len(order)]
        round_before = gs.round_number
        session.handle_message({'type': 'surrender', 'faction': human})
        self.assertEqual(gs.active_faction, following)
        self.assertEqual(gs.phase.name, 'PURCHASE')
        self.assertEqual(gs.round_number, round_before + (1 if order.index(following) == 0 else 0))
        session.handle_message({'type': 'choose_end', 'faction': human, 'end': False})
        for _ in range(60):  # (play on: nothing more is ever queued for the faction that gave up)
            queued = [m for m in session.handle_message({'type': 'next'}) if m.get('type') == 'phase_queue']
            self.assertNotIn(human, [m['faction'] for m in queued])


class TestTheLastHumanChooses(unittest.TestCase):
    def setUp(self):
        self.session = game(seat('HUMAN'), bot(), bot())
        [self.human] = humans(self.session)
        out = self.session.handle_message({'type': 'surrender', 'faction': self.human})
        self.asked = [m for m in out if m.get('type') == 'last_human_choice']

    def test_they_are_asked_and_the_game_holds_still(self):
        self.assertEqual(self.asked, [{'type': 'last_human_choice', 'faction': self.human}])
        self.assertEqual(self.session.waiting_for(), [self.human])
        self.assertEqual(self.session.handle_message({'type': 'next'})[0]['type'], 'error')
        self.assertEqual(self.session._follow(None, None)['last_human_choice'], self.human)  # (asked again on entering)

    def test_ending_it_is_an_armistice_among_those_left_and_the_one_ahead_wins(self):
        out = self.session.handle_message({'type': 'choose_end', 'faction': self.human, 'end': True})
        gs = self.session.engine.game_state
        self.assertTrue(gs.game_over)
        [report] = [m['report'] for m in out if m['type'] == 'game_over']
        status = {r['faction']: r['victory_status'] for r in report}
        self.assertEqual(status[self.human], 'Surrendered')
        self.assertEqual(sorted(v for f, v in status.items() if f != self.human), ['Armistice', 'Winner'])
        [armistice] = [e for m in out if m['type'] == 'armistice_resolved' for e in m['events'] if e['kind'] == 'armistice']
        self.assertEqual(armistice['after_surrender'], self.human)

    def test_letting_it_go_on_the_bots_play_it_out(self):
        out = self.session.handle_message({'type': 'choose_end', 'faction': self.human, 'end': False})
        self.assertEqual(out, [{'type': 'last_human_choice_made', 'faction': self.human, 'end': False}])
        self.assertEqual(self.session.waiting_for(), [])
        gs = self.session.engine.game_state
        for _ in range(3000):
            if gs.game_over:
                break
            self.session.handle_message({'type': 'next'})
        self.assertTrue(gs.game_over)

    def test_only_they_choose_and_only_once(self):
        bot_faction = next(iter(self.session.bots))
        self.assertEqual(self.session.handle_message({'type': 'choose_end', 'faction': bot_faction, 'end': True})[0]['type'],
                         'error')
        self.session.handle_message({'type': 'choose_end', 'faction': self.human, 'end': False})
        self.assertEqual(self.session.handle_message({'type': 'choose_end', 'faction': self.human, 'end': True})[0]['type'],
                         'error')

    def test_a_restart_keeps_the_question(self):
        recorded = start({'seats': [seat('HUMAN'), bot(), bot()] + [OFF] * 3, 'seed': 5, 'armistice_rounds': 0})
        [human] = humans(recorded.session)
        recorded.handle_message({'type': 'surrender', 'faction': human})
        again = reload(recorded)
        self.assertEqual(again.session._last_human_choice, human)
        self.assertEqual(again.session.waiting_for(), [human])


class TestInAMultiPlayerGame(SeatTest):
    def test_the_player_is_asked_through_the_hub_and_then_watches(self):
        # (one human, the host: the game starts at once, Ann in its seat)
        reply = self.send('ann', {'type': 'create_game', 'scenario': {'kind': 'fixed', 'id': 'fixed'},
                                  'settings': settings(seat('HUMAN'), bot(), bot(), seed=61, armistice_rounds=2)})
        gid = next(m['game']['id'] for m in self.got(reply, 'ann') if m['type'] == 'entered_game')
        self.run_game(gid)
        mine = self.faction(gid, self.ann)
        out = self.send('ann', {'type': 'surrender', 'faction': mine})
        self.assertIn({'type': 'last_human_choice', 'faction': mine},
                      [{k: m[k] for k in ('type', 'faction')} for m in self.got(out, 'ann') if m.get('type') == 'last_human_choice'])
        self.assertEqual(self.session(gid).waiting_for(), [mine])
        self.assertEqual(games.get(self.store, gid)['progress']['waiting_for'], [self.ann])  # ("Your move" in Resume)
        self.send('ann', {'type': 'choose_end', 'faction': mine, 'end': False})
        self.assertEqual(self.session(gid).waiting_for(), [])
        # Out of the game but still seated, Ann answers the bots' armistice as a spectator.
        for _ in range(4000):
            if self.session(gid).waiting_for() == ['spectator'] or self.session(gid).engine.game_state.game_over:
                break
            inner = self.session(gid).session  # (an all-bot game keeps pace with its viewer: as if Ann watched on)
            inner.viewer_seq = inner.seq
            if gid in self.hub.runnable():
                self.hub.step(gid)
        self.assertEqual(self.session(gid).waiting_for(), ['spectator'])  # (the bots' armistice, two rounds in)
        self.assertNotIn('error', [m['type'] for m in self.got(
            self.send('ann', {'type': 'respond_armistice', 'accept': True}), 'ann')])
        self.assertTrue(self.session(gid).engine.game_state.game_over)


class TestAnotherHumanStillPlaying(unittest.TestCase):
    def test_nobody_is_asked(self):
        session = game(seat('HUMAN'), seat('HUMAN'), bot())
        first = humans(session)[0]
        out = session.handle_message({'type': 'surrender', 'faction': first})
        self.assertNotIn('last_human_choice', [m['type'] for m in out])
        self.assertNotIn(first, session.waiting_for())


if __name__ == '__main__':
    unittest.main()
