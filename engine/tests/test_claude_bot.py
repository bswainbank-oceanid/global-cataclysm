"""
engine.bots.claude_bot.ClaudeBot -- no real API calls (no cost, deterministic, no network): a fake
client stands in for anthropic.Anthropic(), returning pre-scripted tool_use responses.
"""
import os
import random
import unittest
from unittest import mock

from engine.bots.claude_bot import ClaudeBot
from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit


class FakeBlock:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)  # list of "content" (list[FakeBlock]), one per call
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError('FakeMessages ran out of scripted responses')
        return mock.Mock(content=self.responses.pop(0))


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def tool_use(name, input_, id='t1'):
    return FakeBlock('tool_use', id=id, name=name, input=input_)


def world(faction='NAA', treasury=100, territories=None, adjacency=None, units_by_territory=None,
          modes=None, phase=Phase.PURCHASE):
    data = FakeData(
        territories=territories or {1: {'type': 'land', 'value': 5, 'strategic_center': True}, 2: {'type': 'land', 'value': 3}},
        adjacency=adjacency or {1: [2], 2: [1]},
    )
    modes = modes or {faction: FactionMode.BOT, 'AAC': FactionMode.BOT}
    owners = {1: faction, 2: 'AAC'}
    gs = make_state(data, owners, modes, treasury={faction: treasury}, units_by_territory=units_by_territory, phase=phase)
    engine = GameEngine(gs, data)
    return engine, gs


class TestPurchase(unittest.TestCase):
    def test_decides_via_a_single_tool_call(self):
        engine, gs = world()
        client = FakeClient([[tool_use('submit_purchase', {'orders': [{'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 1}]}) ]])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_purchase_phase()
        self.assertEqual(len(gs.territories[1].pending_deployment), 2)
        self.assertEqual(len(client.messages.calls), 1)

    def test_retries_after_an_illegal_order_then_succeeds(self):
        engine, gs = world()
        client = FakeClient([
            [tool_use('submit_purchase', {'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 2}]})],  # 2 is AAC's, illegal
            [tool_use('submit_purchase', {'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 1}]})],  # fixed
        ])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_purchase_phase()
        self.assertEqual(len(gs.territories[1].pending_deployment), 1)
        self.assertEqual(len(client.messages.calls), 2)
        # the retry actually told the model what went wrong
        second_call_messages = client.messages.calls[1]['messages']
        retry_text = str(second_call_messages)
        self.assertIn('rejected', retry_text)

    def test_falls_back_to_no_purchase_after_max_rounds(self):
        engine, gs = world()
        # every response is empty (no tool_use at all) -- the model never decides
        client = FakeClient([[FakeBlock('text', text='thinking...')] for _ in range(3)])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client, max_tool_rounds=3)
        bot.take_purchase_phase()
        self.assertEqual(gs.territories[1].pending_deployment, [])
        self.assertEqual(len(client.messages.calls), 3)

    def test_a_network_error_falls_back_safely_without_raising(self):
        engine, gs = world()
        client = mock.Mock()
        client.messages.create.side_effect = RuntimeError('connection reset')
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_purchase_phase()  # must not raise
        self.assertEqual(gs.territories[1].pending_deployment, [])


class TestCombatMove(unittest.TestCase):
    def test_can_call_battle_sim_before_deciding(self):
        mover = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        engine, gs = world(units_by_territory={1: [mover], 2: [defender]}, phase=Phase.COMBAT_MOVE)
        client = FakeClient([
            [tool_use('battle_sim_estimate', {'unit_ids': [mover.unit_id], 'target': 2}, id='e1')],
            [tool_use('submit_combat_moves', {'orders': [{'unit_id': mover.unit_id, 'path': [1, 2]}]})],
        ])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_combat_move_phase()
        self.assertEqual(len(client.messages.calls), 2)
        # the estimate's result really was sent back as this round's tool_result
        second_call_messages = client.messages.calls[1]['messages']
        self.assertIn('attacker_wins', str(second_call_messages))
        self.assertTrue(mover.has_moved_combat)

    def test_empty_orders_is_a_legal_do_nothing_decision(self):
        mover = make_unit('Infantry', 'NAA')
        engine, gs = world(units_by_territory={1: [mover]}, phase=Phase.COMBAT_MOVE)
        client = FakeClient([[tool_use('submit_combat_moves', {'orders': []})]])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_combat_move_phase()  # must not raise
        self.assertFalse(mover.has_moved_combat)


class TestDiplomacy(unittest.TestCase):
    def test_an_ineligible_invite_target_is_rejected_then_retried(self):
        engine, gs = world(modes={'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT, 'GPC': FactionMode.BOT}, phase=Phase.DIPLOMACY)
        client = FakeClient([
            [tool_use('submit_diplomacy', {'alliance_action': {'action': 'invite', 'target': 'ZZZ'}, 'surrender_demands': []})],
            [tool_use('submit_diplomacy', {'alliance_action': {'action': 'invite', 'target': 'AAC'}, 'surrender_demands': []})],
        ])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_diplomacy_phase()
        self.assertEqual(len(client.messages.calls), 2)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['AAC'].alliance)
        self.assertIsNotNone(gs.factions['NAA'].alliance)

    def test_none_action_with_no_demands_is_legal(self):
        engine, gs = world(phase=Phase.DIPLOMACY)
        client = FakeClient([[tool_use('submit_diplomacy', {'alliance_action': {'action': 'none'}, 'surrender_demands': []})]])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        bot.take_diplomacy_phase()  # must not raise
        self.assertIsNone(gs.factions['NAA'].alliance)


class TestConstruction(unittest.TestCase):
    def test_missing_api_key_raises_clearly(self):
        engine, gs = world()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                ClaudeBot(engine, 'NAA', rng=random.Random(1))
        self.assertIn('ANTHROPIC_API_KEY', str(ctx.exception))

    def test_default_client_is_never_constructed_when_one_is_injected(self):
        engine, gs = world()
        client = FakeClient([])
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=client)
        self.assertIs(bot.client, client)

    def test_default_model(self):
        engine, gs = world()
        bot = ClaudeBot(engine, 'NAA', rng=random.Random(1), client=FakeClient([]))
        self.assertEqual(bot.model, 'claude-haiku-4-5-20251001')


if __name__ == '__main__':
    unittest.main()
