import unittest

from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase
from server.session import GameSession


def _solo_session():
    """NAA the only HUMAN faction, everyone else NEUTRAL -- the same
    demo scenario server.app._build_demo_session builds. Only 1 active
    faction, so would_game_end() is True the moment NAA's own turn
    reaches process_game_end_check -- deliberately the smallest possible
    game, one full turn then over."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    return GameSession(GameEngine(gs, None))


def _two_human_session():
    """NAA and UE both HUMAN, everyone else NEUTRAL -- 2 active
    factions, so the game keeps going turn after turn (not mutually
    allied, and len(active) > 1) -- lets a test actually observe
    your_turn switching from one faction to the other."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['UE'] = FactionMode.HUMAN
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    return GameSession(GameEngine(gs, None))


class TestConnect(unittest.TestCase):
    def test_unknown_faction_errors(self):
        session = _solo_session()
        messages = session.connect('ZZZ')
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['type'], 'error')
        self.assertEqual(messages[0]['to'], 'ZZZ')

    def test_non_human_faction_errors(self):
        session = _solo_session()
        messages = session.connect('UE')  # NEUTRAL in this scenario
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn('not a HUMAN', messages[0]['message'])

    def test_active_human_faction_gets_state_and_your_turn(self):
        session = _solo_session()
        messages = session.connect('NAA')
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'your_turn'])
        self.assertEqual(messages[1]['faction'], 'NAA')
        self.assertEqual(messages[1]['phase'], 'PURCHASE')

    def test_inactive_human_faction_gets_state_but_no_your_turn(self):
        session = _two_human_session()  # NAA goes first (randomize_play_order=False)
        messages = session.connect('UE')
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state'])


class TestHandleMessage(unittest.TestCase):
    def test_unknown_message_type_errors(self):
        session = _solo_session()
        messages = session.handle_message({'type': 'do_a_barrel_roll', 'faction': 'NAA'})
        self.assertEqual(messages[0]['type'], 'error')
        self.assertEqual(messages[0]['to'], 'NAA')

    def test_submit_purchases_out_of_turn_is_rejected(self):
        session = _two_human_session()
        messages = session.handle_message({'type': 'submit_purchases', 'faction': 'UE', 'orders': []})
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn("not UE's turn", messages[0]['message'])

    def test_malformed_order_is_rejected(self):
        session = _solo_session()
        messages = session.handle_message({
            'type': 'submit_purchases', 'faction': 'NAA', 'orders': [{'unit_type': 'Infantry'}],  # missing qty/deploy_at
        })
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn('malformed order', messages[0]['message'])

    def test_illegal_purchase_is_rejected_by_the_real_engine(self):
        session = _solo_session()
        # a territory_id that doesn't exist on the map reliably raises
        # regardless of the real map's layout.
        messages = session.handle_message({
            'type': 'submit_purchases', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': -1}],
        })
        self.assertEqual(messages[0]['type'], 'error')

    def test_submit_purchases_stages_without_committing(self):
        session = _solo_session()
        gs = session.engine.game_state
        owned = next(tid for tid, t in gs.territories.items() if t.owner == 'NAA')
        messages = session.handle_message({
            'type': 'submit_purchases', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': owned}],
        })
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['type'], 'purchases_staged')
        self.assertEqual(messages[0]['to'], 'NAA')
        self.assertGreater(messages[0]['total_cost'], 0)
        self.assertEqual(gs.territories[owned].pending_deployment, [], 'staged only -- not committed until confirm')

    def test_confirm_with_no_staged_orders_drains_to_game_over_in_the_solo_scenario(self):
        session = _solo_session()
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'game_over'], 'only 1 active faction -- would_game_end() is true immediately')
        self.assertTrue(session.engine.game_state.game_over)

    def test_confirm_out_of_turn_is_rejected(self):
        session = _two_human_session()
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'UE'})
        self.assertEqual(messages[0]['type'], 'error')

    def test_confirm_drains_through_to_the_next_factions_turn(self):
        session = _two_human_session()
        gs = session.engine.game_state
        self.assertEqual(gs.active_faction, 'NAA')

        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'your_turn'])
        self.assertEqual(messages[1]['faction'], 'UE', "advance_turn should have moved play on to UE")
        self.assertEqual(messages[1]['phase'], 'PURCHASE')
        self.assertEqual(gs.active_faction, 'UE')
        self.assertEqual(gs.phase, Phase.PURCHASE)
        self.assertFalse(gs.game_over)

    def test_full_purchase_and_deploy_actually_lands_units_on_the_board(self):
        # End-to-end proof the whole staged-then-committed-then-drained
        # pipeline really places units, not just that the messages look
        # right -- deploy_and_collect_income runs as part of draining
        # through the automatic phases.
        session = _solo_session()
        gs = session.engine.game_state
        owned = next(tid for tid, t in gs.territories.items() if t.owner == 'NAA')
        session.handle_message({
            'type': 'submit_purchases', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': owned}],
        })
        before = len(gs.territories[owned].units)
        session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        after = len(gs.territories[owned].units)
        self.assertEqual(after, before + 1)


if __name__ == '__main__':
    unittest.main()
