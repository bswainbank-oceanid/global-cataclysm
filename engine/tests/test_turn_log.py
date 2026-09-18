import unittest

from engine.engine import GameEngine, PurchaseOrder, CombatMoveOrder
from engine.state import FactionMode, Phase
from engine.turn_log import TurnLog
from engine.tests.test_engine import FakeData, ScriptedRNG, make_state, make_unit


class TestTurnLogDirectly(unittest.TestCase):
    """No engine involved -- just the record_* methods' own dict shape."""

    def test_record_purchase(self):
        log = TurnLog()
        log.record_purchase('NAA', [PurchaseOrder('Infantry', 2, 1)], 8)
        self.assertEqual(log.events, [{
            'kind': 'purchase', 'faction': 'NAA', 'total_cost': 8,
            'orders': [{'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 1}],
        }])

    def test_record_combat_move(self):
        log = TurnLog()
        log.record_combat_move('NAA', [CombatMoveOrder(5, [1, 2])])
        self.assertEqual(log.events, [{
            'kind': 'combat_move', 'faction': 'NAA',
            'orders': [{'unit_id': 5, 'path': [1, 2]}],
        }])

    def test_record_capture(self):
        log = TurnLog()
        log.record_capture(7, 'NAA', 3, 'AAC')
        self.assertEqual(log.events, [{
            'kind': 'territory_captured', 'turn': 7, 'faction': 'NAA', 'territory_id': 3, 'previous_owner': 'AAC',
        }])

    def test_record_deploy(self):
        log = TurnLog()
        log.record_deploy('NAA', 3, 'Infantry', 2)
        self.assertEqual(log.events, [{
            'kind': 'unit_deployed', 'faction': 'NAA', 'territory_id': 3, 'unit_type': 'Infantry', 'qty': 2,
        }])

    def test_record_income(self):
        log = TurnLog()
        log.record_income('NAA', 31)
        self.assertEqual(log.events, [{'kind': 'income_collected', 'faction': 'NAA', 'amount': 31}])

    def test_record_elimination(self):
        log = TurnLog()
        log.record_elimination('AAC')
        self.assertEqual(log.events, [{'kind': 'faction_eliminated', 'faction': 'AAC'}])

    def test_events_accumulate_in_order_across_calls(self):
        log = TurnLog()
        log.record_income('NAA', 10)
        log.record_elimination('AAC')
        self.assertEqual([e['kind'] for e in log.events], ['income_collected', 'faction_eliminated'])


class TestTurnLogEngineIntegration(unittest.TestCase):
    def test_confirm_purchases_logs_the_orders(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])
        engine.confirm_purchases('NAA')
        self.assertEqual(len(log.events), 1)
        self.assertEqual(log.events[0]['kind'], 'purchase')
        self.assertEqual(log.events[0]['orders'], [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 1}])

    def test_confirm_combat_moves_logs_the_orders(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover]},
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(log.events, [{
            'kind': 'combat_move', 'faction': 'NAA',
            'orders': [{'unit_id': mover.unit_id, 'path': [1, 2]}],
        }])

    def test_resolve_combat_logs_roll_by_roll_events(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        # Attacker rolls the D6 die max (6) -- an auto-hit, kills the
        # hp-2 defender outright. The defender still rolls too (removal
        # is deferred to the end of the round) -- 1 is a safe miss,
        # irrelevant to this test either way -- and the battle ends
        # after round 1 since the defender's side is now empty.
        engine.resolve_combat('NAA', rng=ScriptedRNG([6, 1]))

        kinds = [e['event_kind'] for e in log.events if e['kind'] == 'battle_event']
        self.assertIn('UNIT_ROLL', kinds)
        self.assertIn('BATTLE_END', kinds)
        roll_event = next(e for e in log.events if e.get('event_kind') == 'UNIT_ROLL')
        self.assertEqual(roll_event['territory_id'], 1)
        self.assertEqual(roll_event['battle_type'], 'land')
        self.assertEqual(roll_event['unit_id'], attacker.unit_id)
        self.assertEqual(roll_event['owner'], 'NAA')
        self.assertEqual(roll_event['roll'], 6)
        self.assertTrue(roll_event['hit'])
        self.assertEqual(roll_event['target_unit_id'], defender.unit_id)
        self.assertEqual(roll_event['target_owner'], 'AAC')

        end_event = next(e for e in log.events if e.get('event_kind') == 'BATTLE_END')
        self.assertEqual(end_event['outcome'], 'defender_eliminated')
        self.assertIn(defender.unit_id, end_event['eliminated_defender_ids'])

    def test_true_territory_loss_capture_is_logged(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC')
        attacker_unit = make_unit('Infantry', 'X')
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': FactionMode.HUMAN, 'X': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'AAC', 'X'}}, units_by_territory={1: [defender_unit, attacker_unit]}, global_turn=4,
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 5]))
        capture_events = [e for e in log.events if e['kind'] == 'territory_captured']
        self.assertEqual(capture_events, [{
            'kind': 'territory_captured', 'turn': 4, 'faction': 'X', 'territory_id': 1, 'previous_owner': 'AAC',
        }])

    def test_process_capture_territory_logs_a_real_capture_but_not_a_reaffirmed_one(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover]},
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')

        capture_events = [e for e in log.events if e['kind'] == 'territory_captured']
        self.assertEqual(len(capture_events), 1)
        self.assertEqual(capture_events[0]['faction'], 'NAA')
        self.assertEqual(capture_events[0]['territory_id'], 2)
        self.assertEqual(capture_events[0]['previous_owner'], 'AAC')

    def test_deploy_and_collect_income_logs_deploys_and_income(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME, treasury={'NAA': 10},
            pending_by_territory={1: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'NAA')]},
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.deploy_and_collect_income('NAA')

        deploy_events = [e for e in log.events if e['kind'] == 'unit_deployed']
        self.assertEqual(deploy_events, [{'kind': 'unit_deployed', 'faction': 'NAA', 'territory_id': 1, 'unit_type': 'Infantry', 'qty': 2}])
        income_events = [e for e in log.events if e['kind'] == 'income_collected']
        self.assertEqual(income_events, [{'kind': 'income_collected', 'faction': 'NAA', 'amount': 3}])

    def test_process_elimination_check_logs_elimination(self):
        data = FakeData(territories={1: {'type': 'land', 'strategic_center': True}}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
        )
        # NAA owns nothing, AAC owns the only SC -- NAA has 0 SCs.
        gs.territories[1].owner = 'AAC'
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.process_elimination_check()
        self.assertIn({'kind': 'faction_eliminated', 'faction': 'NAA'}, log.events)

    def test_invite_and_withdraw_are_logged(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.ALLIANCES,
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.invite_to_alliance('NAA', 'UE', target_accepts=True)
        self.assertEqual(log.events[-1]['kind'], 'alliance_joined')
        self.assertTrue(log.events[-1]['new_alliance'])

        gs.phase = Phase.ALLIANCES
        engine._alliance_action_taken.discard('NAA')
        engine.withdraw_from_alliance('NAA')
        self.assertEqual(log.events[-1]['kind'], 'alliance_withdrawal')
        self.assertEqual(log.events[-1]['former_members'], ['UE'])


if __name__ == '__main__':
    unittest.main()
