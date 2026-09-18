import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase, UnitInstance
from engine.turn_log import TurnLog
from server.session import GameSession


def _session(modes, bot_factions=()):
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log)
    bots = {code: RandomBot(engine, code, rng=random.Random(1)) for code in bot_factions}
    return GameSession(engine, turn_log, bots)


def _solo_session():
    """NAA the only HUMAN faction, everyone else NEUTRAL -- only 1 active
    faction, so would_game_end() is True the moment NAA's own turn
    reaches process_game_end_check -- deliberately the smallest possible
    game, one full turn then over."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    return _session(modes)


def _two_human_session():
    """NAA and UE both HUMAN, everyone else NEUTRAL -- 2 active
    factions, so the game keeps going turn after turn (not mutually
    allied, and len(active) > 1) -- lets a test actually observe
    your_turn switching from one faction to the other."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['UE'] = FactionMode.HUMAN
    return _session(modes)


def _human_and_bot_session():
    """NAA HUMAN, AAC BOT, everyone else NEUTRAL -- the same shape as
    server.app._build_demo_session, for exercising bot-turn playback."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['AAC'] = FactionMode.BOT
    return _session(modes, bot_factions=['AAC'])


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

    def test_your_turn_includes_legal_purchase_targets(self):
        session = _solo_session()
        messages = session.connect('NAA')
        targets = messages[1]['legal_purchase_targets']
        self.assertIn('sc_targets', targets)
        self.assertIn('other_targets', targets)
        # NAA starts with at least one owned Strategic Center.
        self.assertGreater(len(targets['sc_targets']), 0)

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


class TestBotTurnPlayback(unittest.TestCase):
    def test_confirming_the_humans_turn_plays_out_the_bots_whole_turn(self):
        session = _human_and_bot_session()
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        types = [m['type'] for m in messages]
        # NAA's own turn has no battles to report (nothing to attack yet),
        # so no "combat_events" for NAA; then AAC's whole turn plays out
        # as one "bot_turn", then back to NAA.
        self.assertEqual(types, ['state', 'bot_turn', 'state', 'your_turn'])
        self.assertEqual(messages[1]['faction'], 'AAC')
        self.assertGreater(len(messages[1]['events']), 0, "a bot's turn should produce at least a purchase event")
        self.assertEqual(messages[3]['faction'], 'NAA', "control returns to the human after the bot's turn")

    def test_bot_turn_events_include_a_purchase_and_income(self):
        session = _human_and_bot_session()
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        bot_turn = next(m for m in messages if m['type'] == 'bot_turn')
        kinds = {e['kind'] for e in bot_turn['events']}
        self.assertIn('purchase', kinds)
        self.assertIn('income_collected', kinds)

    def test_bot_turn_events_are_only_that_bots_own_turn_not_the_humans(self):
        session = _human_and_bot_session()
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        bot_turn = next(m for m in messages if m['type'] == 'bot_turn')
        for event in bot_turn['events']:
            self.assertNotEqual(event.get('faction'), 'NAA')

    def test_second_confirm_by_the_human_plays_another_bot_turn(self):
        session = _human_and_bot_session()
        session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        types = [m['type'] for m in messages]
        self.assertIn('bot_turn', types)


class TestHumanCombatPlayback(unittest.TestCase):
    """No message type lets a human submit a real attack yet (Combat
    Move is auto-submitted empty for a human -- see this module's own
    docstring), so the only way a HUMAN'S OWN Combat Resolution produces
    battle events today is a standing contest already in place before
    their turn starts (an enemy attacked on an earlier turn). This
    proves the underlying combat_events mechanism itself works
    correctly for a human's turn, same as it does for a bot's."""

    def test_a_standing_contest_on_the_humans_own_turn_is_surfaced_as_combat_events(self):
        session = _solo_session()
        gs = session.engine.game_state
        owned = next(tid for tid, t in gs.territories.items() if t.owner == 'NAA')
        naa_unit = next((u for u in gs.territories[owned].units if u.owner == 'NAA'), None)
        self.assertIsNotNone(naa_unit, 'NAA should already have a starting unit on its own territory')

        enemy_unit = UnitInstance(unit_id=99999, unit_type='Infantry', owner='AAC', current_hp=2)
        gs.territories[owned].units.append(enemy_unit)
        gs.territories[owned].contested_by = {'NAA', 'AAC'}
        # AAC is NEUTRAL in this solo scenario -- switch it to HUMAN
        # (not BOT) purely so it counts as an active, non-allied combatant;
        # it never actually takes a turn in this test.
        gs.factions['AAC'].mode = FactionMode.HUMAN

        messages = session.handle_message({'type': 'confirm_purchases', 'faction': 'NAA'})
        combat_msgs = [m for m in messages if m['type'] == 'combat_events']
        self.assertEqual(len(combat_msgs), 1)
        self.assertEqual(combat_msgs[0]['faction'], 'NAA')
        kinds = {e['event_kind'] for e in combat_msgs[0]['events']}
        self.assertIn('UNIT_ROLL', kinds)
        # Only battle events -- not this same turn's purchase/deploy/income.
        self.assertTrue(all(e['kind'] == 'battle_event' for e in combat_msgs[0]['events']))


if __name__ == '__main__':
    unittest.main()
