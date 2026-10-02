"""The automatic armistice proposal (the 'Rounds until armistice proposal' setting): once that many rounds
are played the game proposes an armistice itself -- bots accept at once, a human is asked -- and again every
5 rounds after a decline; 0 never proposes."""
import unittest

from server.lobby import build_session, check_settings
from server.session import ARMISTICE_REPEAT_ROUNDS

BOT = {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': 'random'}
HUMAN = {'mode': 'HUMAN', 'faction': 'random', 'alliance': 0}
OFF = {'mode': 'NONCOMBATANT', 'faction': 'random', 'alliance': 0}


def game(seats, rounds):
    session, _ = build_session({'seats': seats + [OFF] * (6 - len(seats)), 'seed': 3, 'armistice_rounds': rounds})
    session.handle_message({'type': 'watch'})
    return session


def step_until(session, stop, limit=3000):
    """Presses Next until `stop(messages)`; returns every message seen."""
    seen = []
    for _ in range(limit):
        msgs = session.handle_message({'type': 'next'})
        seen += msgs
        if stop(msgs) or session.engine.game_state.game_over:
            return seen
    raise AssertionError('never stopped')


class TestAutoArmistice(unittest.TestCase):
    def test_an_all_bot_game_ends_by_armistice_after_the_set_rounds(self):
        session = game([BOT, BOT, BOT], 2)
        seen = step_until(session, lambda m: False)
        gs = session.engine.game_state
        self.assertTrue(gs.game_over)
        self.assertEqual(gs.round_number, 3, 'proposed as round 3 begins, once 2 rounds are played')
        armistice = [e for m in seen if m.get('type') == 'armistice_resolved' for e in m['events'] if e['kind'] == 'armistice']
        self.assertEqual(len(armistice), 1)
        self.assertEqual((armistice[0]['faction'], armistice[0]['automatic_after']), (None, 2))
        self.assertEqual(sorted(armistice[0]['participants']), sorted(session.bots))

    def test_a_human_is_asked_and_a_decline_asks_again_five_rounds_later(self):
        session = game([HUMAN, BOT], 1)
        human = next(f for f, st in session.engine.game_state.factions.items() if st.mode.name == 'HUMAN')
        proposed = lambda msgs: any(m.get('type') == 'armistice_proposed' for m in msgs)  # noqa: E731
        seen = step_until(session, proposed)
        first = next(m for m in seen if m.get('type') == 'armistice_proposed')
        self.assertEqual((first['from'], first['awaiting'], first['automatic_after']), (None, [human], 1))
        self.assertEqual(session.handle_message({'type': 'next'})[0]['type'], 'error', 'the game waits for the answer')
        declined = session.handle_message({'type': 'respond_armistice', 'faction': human, 'accept': False})[0]
        asked_in = session.engine.game_state.round_number
        self.assertEqual((declined['accepted'], declined['next_round']), (False, asked_in + ARMISTICE_REPEAT_ROUNDS))
        step_until(session, proposed)
        self.assertEqual(session.engine.game_state.round_number, asked_in + ARMISTICE_REPEAT_ROUNDS)
        done = session.handle_message({'type': 'respond_armistice', 'faction': human, 'accept': True})
        self.assertTrue(session.engine.game_state.game_over)
        self.assertEqual([m['type'] for m in done][:1], ['armistice_resolved'])

    def test_zero_never_proposes(self):
        session = game([BOT, BOT], 0)
        for _ in range(400):
            session.handle_message({'type': 'next'})
        self.assertFalse(session.engine.game_state.game_over and
                         any(e['kind'] == 'armistice' for e in session.turn_log.events))

    def test_the_setting_is_checked(self):
        base = {'seats': [BOT, BOT] + [OFF] * 4}
        self.assertEqual(check_settings(dict(base, armistice_rounds=100)), [])
        for bad in (-1, 101, 2.5, 'ten', True):
            self.assertTrue(check_settings(dict(base, armistice_rounds=bad)), bad)


if __name__ == '__main__':
    unittest.main()
