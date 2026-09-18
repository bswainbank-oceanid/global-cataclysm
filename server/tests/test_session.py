import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase, UnitInstance
from engine.turn_log import TurnLog
from server.session import GameSession


def _session(modes, bot_factions=(), **build_kwargs):
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False, **build_kwargs)
    turn_log = TurnLog()
    # combat_rng must be seeded -- GameEngine defaults to an unseeded
    # random.Random() otherwise, which made any test that actually
    # triggers combat resolution (e.g. TestCombatMovePlayback) flaky:
    # its dice-dependent outcome (attacker wins outright vs. battle still
    # contested after 3 rounds) differed from run to run even though
    # every other source of randomness here (RandomBot's own rng below)
    # was already seeded.
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(1))
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


def _two_human_session_with_combat_moves_allowed():
    """Same as _two_human_session, but with game_start_settings.
    allow_combat_moves_first_turn=True so Combat Move is a real decision
    point from NAA's very first turn, not just its second onward."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['UE'] = FactionMode.HUMAN
    return _session(modes, allow_combat_moves_first_turn=True)


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

    def test_purchase_out_of_turn_is_rejected(self):
        session = _two_human_session()
        messages = session.handle_message({'type': 'purchase', 'faction': 'UE', 'orders': []})
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn("not UE's turn", messages[0]['message'])

    def test_malformed_order_is_rejected(self):
        session = _solo_session()
        messages = session.handle_message({
            'type': 'purchase', 'faction': 'NAA', 'orders': [{'unit_type': 'Infantry'}],  # missing qty/deploy_at
        })
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn('malformed order', messages[0]['message'])

    def test_illegal_purchase_is_rejected_by_the_real_engine(self):
        session = _solo_session()
        # a territory_id that doesn't exist on the map reliably raises
        # regardless of the real map's layout.
        messages = session.handle_message({
            'type': 'purchase', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': -1}],
        })
        self.assertEqual(messages[0]['type'], 'error')

    def test_rejected_purchase_commits_nothing(self):
        session = _solo_session()
        gs = session.engine.game_state
        owned = next(tid for tid, t in gs.territories.items() if t.owner == 'NAA')
        before = gs.factions['NAA'].treasury_mpc
        session.handle_message({
            'type': 'purchase', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': -1}],  # illegal target
        })
        self.assertEqual(gs.territories[owned].pending_deployment, [])
        self.assertEqual(gs.factions['NAA'].treasury_mpc, before)

    def test_purchase_with_no_orders_drains_to_game_over_in_the_solo_scenario(self):
        session = _solo_session()
        messages = session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'NONCOMBAT_MOVE', 'Non-Combat Move is a real decision point now too')

        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'game_over'], 'only 1 active faction -- would_game_end() is true immediately')
        self.assertTrue(session.engine.game_state.game_over)

    def test_purchase_drains_through_to_the_next_factions_turn(self):
        session = _two_human_session()
        gs = session.engine.game_state
        self.assertEqual(gs.active_faction, 'NAA')

        messages = session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'NONCOMBAT_MOVE')

        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'your_turn'])
        self.assertEqual(messages[1]['faction'], 'UE', "advance_turn should have moved play on to UE")
        self.assertEqual(messages[1]['phase'], 'PURCHASE')
        self.assertEqual(gs.active_faction, 'UE')
        self.assertEqual(gs.phase, Phase.PURCHASE)
        self.assertFalse(gs.game_over)

    def test_full_purchase_and_deploy_actually_lands_units_on_the_board(self):
        # End-to-end proof the whole validate-then-commit-then-drained
        # pipeline really places units, not just that the messages look
        # right -- deploy_and_collect_income runs as part of draining
        # through the automatic phases once Non-Combat Move (a real
        # decision point now) is also confirmed.
        session = _solo_session()
        gs = session.engine.game_state
        owned = next(tid for tid, t in gs.territories.items() if t.owner == 'NAA')
        before = len(gs.territories[owned].units)
        session.handle_message({
            'type': 'purchase', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': owned}],
        })
        session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        after = len(gs.territories[owned].units)
        self.assertEqual(after, before + 1)


class TestBotTurnPlayback(unittest.TestCase):
    def test_confirming_the_humans_turn_plays_out_the_bots_whole_turn(self):
        session = _human_and_bot_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
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
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        bot_turn = next(m for m in messages if m['type'] == 'bot_turn')
        kinds = {e['kind'] for e in bot_turn['events']}
        self.assertIn('purchase', kinds)
        self.assertIn('income_collected', kinds)

    def test_bot_turn_events_are_only_that_bots_own_turn_not_the_humans(self):
        session = _human_and_bot_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        bot_turn = next(m for m in messages if m['type'] == 'bot_turn')
        for event in bot_turn['events']:
            self.assertNotEqual(event.get('faction'), 'NAA')

    def test_second_purchase_by_the_human_plays_another_bot_turn(self):
        # game_start_settings.allow_combat_moves_first_turn defaults
        # False, so NAA's FIRST turn skips Combat Move entirely -- Non-
        # Combat Move is still a real decision point every turn though,
        # so it needs an explicit (empty) submission before AAC's first
        # bot turn plays. By NAA's SECOND turn, Combat Move is no longer
        # skipped either, so both decisions are needed again.
        session = _human_and_bot_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        session.handle_message({'type': 'combat_move', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'NAA', 'orders': []})
        types = [m['type'] for m in messages]
        self.assertIn('bot_turn', types)


class TestHumanCombatPlayback(unittest.TestCase):
    """A standing contest already in place before a human's turn starts
    (an enemy attacked on an earlier turn, not this same turn's own
    Combat Move) also produces battle events on THIS faction's Combat
    Resolution -- proving the underlying combat_events mechanism works
    for a human's turn from either source (a fresh attack via
    TestCombatMovePlayback below, or an old stalemate like this one),
    not just a bot's."""

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

        messages = session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        combat_msgs = [m for m in messages if m['type'] == 'combat_events']
        self.assertEqual(len(combat_msgs), 1)
        self.assertEqual(combat_msgs[0]['faction'], 'NAA')
        kinds = {e['event_kind'] for e in combat_msgs[0]['events']}
        self.assertIn('UNIT_ROLL', kinds)
        # Only battle events -- not this same turn's purchase/deploy/income.
        self.assertTrue(all(e['kind'] == 'battle_event' for e in combat_msgs[0]['events']))


def _make_a_neighbor_hostile(engine, faction, enemy_faction):
    """Reassigns a real adjacent LAND territory of one of `faction`'s own
    owned territories to `enemy_faction`, with one defending Infantry --
    a deterministic, reachable attack target using the REAL map's
    adjacency graph (so any path computed against it is genuinely legal),
    regardless of where the scenario's active factions actually happen to
    sit continent-wise. Returns (attacker_unit, target_territory_id)."""
    terrs = engine.data.territories()
    adjacency = engine.data.adjacency()
    gs = engine.game_state
    for tid, t in gs.territories.items():
        if t.owner != faction:
            continue
        attacker = next((u for u in t.units if u.owner == faction), None)
        if attacker is None:
            continue
        neighbor = next((n for n in adjacency.get(tid, []) if terrs[n]['type'] == 'land'), None)
        if neighbor is None:
            continue
        gs.territories[neighbor].owner = enemy_faction
        gs.territories[neighbor].units.append(
            UnitInstance(unit_id=77777, unit_type='Infantry', owner=enemy_faction, current_hp=2)
        )
        return attacker, neighbor
    raise AssertionError(f'no owned territory of {faction} with both a unit and a land neighbor was found')


class TestCombatMovePlayback(unittest.TestCase):
    """Combat Move as a real human decision point: "the legal combat
    move options for each unit is known at turn start. The client can
    pick from those options and send to server" (this session)."""

    def test_your_turn_for_combat_move_includes_legal_options(self):
        session = _two_human_session_with_combat_moves_allowed()
        messages = session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'COMBAT_MOVE')
        self.assertIn('legal_combat_moves', prompt)
        self.assertNotIn('legal_purchase_targets', prompt)

    def test_reconnecting_mid_turn_at_combat_move_gets_the_same_prompt(self):
        session = _two_human_session_with_combat_moves_allowed()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.connect('NAA')
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'COMBAT_MOVE')

    def test_combat_move_out_of_turn_is_rejected(self):
        session = _two_human_session_with_combat_moves_allowed()
        messages = session.handle_message({'type': 'combat_move', 'faction': 'UE', 'orders': []})
        self.assertEqual(messages[0]['type'], 'error')

    def test_malformed_combat_move_order_is_rejected(self):
        session = _two_human_session_with_combat_moves_allowed()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({
            'type': 'combat_move', 'faction': 'NAA', 'orders': [{'unit_id': 1}],  # missing path
        })
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn('malformed order', messages[0]['message'])

    def test_illegal_combat_move_is_rejected_by_the_real_engine(self):
        session = _two_human_session_with_combat_moves_allowed()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({
            'type': 'combat_move', 'faction': 'NAA',
            'orders': [{'unit_id': 999999, 'path': [1, 2]}],  # no such unit
        })
        self.assertEqual(messages[0]['type'], 'error')

    def test_picking_a_legal_option_actually_attacks_and_continues_the_turn(self):
        session = _two_human_session_with_combat_moves_allowed()
        gs = session.engine.game_state
        attacker, target = _make_a_neighbor_hostile(session.engine, 'NAA', 'UE')

        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        options = session.engine.legal_combat_move_options('NAA')
        self.assertIn(attacker.unit_id, options, "the freshly-hostile neighbor should be a legal attack target")
        path = options[attacker.unit_id]['destinations'][target]

        messages = session.handle_message({
            'type': 'combat_move', 'faction': 'NAA', 'orders': [{'unit_id': attacker.unit_id, 'path': path}],
        })
        # The attack landed (contested), and the turn continued all the
        # way through Combat Resolution -- reported as combat_events --
        # to wherever NAA's turn ends up next.
        self.assertIn('NAA', gs.territories[target].contested_by)
        types = [m['type'] for m in messages]
        self.assertIn('combat_events', types)
        combat_msgs = [m for m in messages if m['type'] == 'combat_events']
        self.assertEqual(combat_msgs[0]['faction'], 'NAA')


class TestNonCombatMovePlayback(unittest.TestCase):
    """Non-Combat Move as a real human decision point: "non-combat move
    options can change after combat. Should be a similar pattern, get
    the full list of legal options after combat. User submits their
    choices" (this session)."""

    def test_your_turn_for_noncombat_move_includes_legal_options(self):
        session = _two_human_session()
        messages = session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'NONCOMBAT_MOVE')
        self.assertIn('legal_noncombat_moves', prompt)
        self.assertNotIn('legal_combat_moves', prompt)

    def test_reconnecting_mid_turn_at_noncombat_move_gets_the_same_prompt(self):
        session = _two_human_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.connect('NAA')
        prompt = next(m for m in messages if m['type'] == 'your_turn')
        self.assertEqual(prompt['phase'], 'NONCOMBAT_MOVE')

    def test_noncombat_move_out_of_turn_is_rejected(self):
        session = _two_human_session()
        messages = session.handle_message({'type': 'noncombat_move', 'faction': 'UE', 'orders': []})
        self.assertEqual(messages[0]['type'], 'error')

    def test_malformed_noncombat_move_order_is_rejected(self):
        session = _two_human_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({
            'type': 'noncombat_move', 'faction': 'NAA', 'orders': [{'unit_id': 1}],  # missing destination
        })
        self.assertEqual(messages[0]['type'], 'error')
        self.assertIn('malformed order', messages[0]['message'])

    def test_illegal_noncombat_move_is_rejected_by_the_real_engine(self):
        session = _two_human_session()
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})
        messages = session.handle_message({
            'type': 'noncombat_move', 'faction': 'NAA',
            'orders': [{'unit_id': 999999, 'destination': 1}],  # no such unit
        })
        self.assertEqual(messages[0]['type'], 'error')

    def test_picking_a_legal_option_actually_moves_the_unit_and_continues_the_turn(self):
        session = _two_human_session()
        gs = session.engine.game_state
        session.handle_message({'type': 'purchase', 'faction': 'NAA', 'orders': []})

        options = session.engine.legal_noncombat_move_options('NAA')
        self.assertTrue(options, "NAA should have at least one unit with a legal non-combat move")
        unit_id = next(iter(options))
        origin = options[unit_id]['territory_id']
        destination = options[unit_id]['destinations'][0]

        messages = session.handle_message({
            'type': 'noncombat_move', 'faction': 'NAA', 'orders': [{'unit_id': unit_id, 'destination': destination}],
        })
        moved_unit = next(u for t in gs.territories.values() for u in t.units if u.unit_id == unit_id)
        self.assertNotIn(moved_unit, gs.territories[origin].units)
        self.assertIn(moved_unit, gs.territories[destination].units)
        # NAA's turn is fully done (Non-Combat Move was the last real
        # decision point) -- play moves on to UE.
        types = [m['type'] for m in messages]
        self.assertEqual(types, ['state', 'your_turn'])
        self.assertEqual(messages[1]['faction'], 'UE')


if __name__ == '__main__':
    unittest.main()
