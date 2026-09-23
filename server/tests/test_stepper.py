import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase
from engine.turn_log import TurnLog
from engine.tests.test_engine import FakeData, make_state, make_unit
from server.session import GameSession

PHASES = [p.value for p in Phase]


def _watch_session(seed=1):
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.BOT
    modes['AAC'] = FactionMode.BOT
    gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(seed))
    bots = {code: RandomBot(engine, code, rng=random.Random(seed)) for code in ('NAA', 'AAC')}
    return GameSession(engine, turn_log, bots)


def _watch(session):
    """Joins as a watcher and steps past the Start of Turn announcement that opens the
    first turn; returns [state, the Purchase queue], like a plain watch used to."""
    messages = session.handle_message({'type': 'watch'})
    assert messages[1]['phase'] == 'START_OF_TURN', messages[1]['phase']
    after = session.handle_message({'type': 'next'})
    return [after[2], after[1]]


def _by_type(messages, msg_type):
    return [m for m in messages if m['type'] == msg_type]


class TestWatch(unittest.TestCase):
    def test_watch_answers_with_state_and_the_first_queue(self):
        session = _watch_session()
        messages = _watch(session)
        self.assertEqual([m['type'] for m in messages], ['state', 'phase_queue'])
        queue = messages[1]
        self.assertEqual((queue['faction'], queue['phase']), ('NAA', 'PURCHASE'))
        self.assertEqual(queue['events'][0]['kind'], 'purchase')

    def test_queued_orders_are_not_executed_until_next(self):
        session = _watch_session()
        _watch(session)
        gs = session.engine.game_state
        self.assertEqual([e for e in session.turn_log.events if e['kind'] != 'start_of_turn'], [])  # queued, not logged
        treasury = gs.factions['NAA'].treasury_mpc

        messages = session.handle_message({'type': 'next'})
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual(result['phase'], 'PURCHASE')
        self.assertEqual(result['events'][0]['kind'], 'purchase')
        spent = result['events'][0]['total_cost']
        self.assertEqual(gs.factions['NAA'].treasury_mpc, treasury - spent)

    def test_the_executed_purchase_is_exactly_what_was_queued(self):
        session = _watch_session()
        queued = _watch(session)[1]['events'][0]
        executed = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]['events'][0]
        self.assertEqual(queued, executed)

    def test_each_next_moves_one_phase_and_a_full_turn_hands_over_to_the_next_faction(self):
        session = _watch_session()
        _watch(session)
        seen = []
        for _ in range(7):
            messages = session.handle_message({'type': 'next'})
            self.assertEqual([m['type'] for m in messages], ['phase_result', 'phase_queue', 'state'])
            queue = messages[1]
            seen.append((queue['faction'], queue['phase']))
        naa_phases = [p for f, p in seen if f == 'NAA']
        aac_phases = [p for f, p in seen if f == 'AAC']
        self.assertEqual(naa_phases, ['COMBAT_RESOLUTION', 'NONCOMBAT_MOVE', 'CAPTURE', 'DEPLOY_INCOME', 'DIPLOMACY'])
        self.assertEqual(aac_phases, ['START_OF_TURN', 'PURCHASE'])
        self.assertLess(seen.index(('NAA', 'DIPLOMACY')), seen.index(('AAC', 'START_OF_TURN')))

    def test_every_turn_opens_with_a_start_of_turn_naming_the_round_and_turn(self):
        session = _watch_session()
        queue = session.handle_message({'type': 'watch'})[1]
        self.assertEqual((queue['faction'], queue['phase'], queue['skipped']), ('NAA', 'START_OF_TURN', []))
        self.assertEqual(queue['events'], [{'kind': 'start_of_turn', 'faction': 'NAA', 'round': 1, 'turn': 1, 'turns_in_round': 2}])
        result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
        self.assertEqual((result['phase'], result['events']), ('START_OF_TURN', queue['events']))
        self.assertEqual(session.engine.game_state.phase, Phase.PURCHASE)  # an announcement, not an engine phase
        seen = []
        for _ in range(120):
            messages = session.handle_message({'type': 'next'})
            if not _by_type(messages, 'phase_queue'):
                break  # the game ended
            q = _by_type(messages, 'phase_queue')[0]
            if q['phase'] == 'START_OF_TURN':
                seen.append((q['faction'], q['events'][0]['round'], q['events'][0]['turn']))
        expected = [('AAC', 1, 2), ('NAA', 2, 1), ('AAC', 2, 2)]
        self.assertGreaterEqual(len(seen), 2)  # (the game may end early: two bots can eliminate each other)
        self.assertEqual(seen[:3], expected[:len(seen[:3])])

    def test_with_a_maximum_alliance_size_of_one_the_diplomacy_phase_still_runs(self):
        # (it also carries the surrender demands, so it is never skipped)
        session = _watch_session()
        session.engine.game_state.max_alliance_size = 1
        _watch(session)
        seen = []
        for _ in range(7):
            queue = _by_type(session.handle_message({'type': 'next'}), 'phase_queue')[0]
            seen.append((queue['faction'], queue['phase']))
        self.assertEqual(seen, [('NAA', 'COMBAT_RESOLUTION'), ('NAA', 'NONCOMBAT_MOVE'), ('NAA', 'CAPTURE'),
                                ('NAA', 'DEPLOY_INCOME'), ('NAA', 'DIPLOMACY'), ('AAC', 'START_OF_TURN'), ('AAC', 'PURCHASE')])

    def test_the_announcement_does_not_repeat_when_the_turn_is_replanned(self):
        session = _watch_session()
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'next'})
        queue = session.handle_message({'type': 'watch'})[1]
        self.assertEqual(queue['phase'], 'PURCHASE')

    def test_a_skipped_first_turn_combat_move_is_reported_on_the_next_queue(self):
        session = _watch_session()
        _watch(session)
        queue = _by_type(session.handle_message({'type': 'next'}), 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertEqual(queue['skipped'], ['COMBAT_MOVE'])

    def test_capture_and_deploy_queues_preview_what_executing_them_does(self):
        session = _watch_session()
        _watch(session)
        previews = {}
        for _ in range(20):
            messages = session.handle_message({'type': 'next'})
            queue = _by_type(messages, 'phase_queue')[0]
            result = _by_type(messages, 'phase_result')[0]
            if result['phase'] in ('CAPTURE', 'DEPLOY_INCOME'):
                self.assertEqual(previews[(result['faction'], result['phase'])], result['events'])
            previews[(queue['faction'], queue['phase'])] = queue['events']

    def test_dry_run_of_a_preview_leaves_the_real_game_untouched(self):
        session = _watch_session()
        _watch(session)
        for _ in range(4):
            session.handle_message({'type': 'next'})  # up to CAPTURE/DEPLOY queued
        gs = session.engine.game_state
        before = gs.to_dict()
        n_events = len(session.turn_log.events)
        session.stepper._dry_run(lambda sim: sim.deploy_and_collect_income(gs.active_faction))
        self.assertEqual(gs.to_dict(), before)
        self.assertEqual(len(session.turn_log.events), n_events)

    def test_rewatching_returns_the_same_pending_queue_without_replanning(self):
        session = _watch_session()
        first = session.handle_message({'type': 'watch'})[1]
        again = session.handle_message({'type': 'watch'})[1]
        self.assertIs(first, again)

    def test_watch_is_refused_when_a_faction_is_neither_human_nor_a_bot_with_a_bot_attached(self):
        modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
        modes['NAA'] = FactionMode.BOT  # a BOT with no RandomBot attached
        modes['AAC'] = FactionMode.BOT
        gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False)
        turn_log = TurnLog()
        engine = GameEngine(gs, None, turn_log=turn_log)
        session = GameSession(engine, turn_log, {'AAC': RandomBot(engine, 'AAC', rng=random.Random(1))})
        self.assertEqual(session.handle_message({'type': 'watch'})[0]['type'], 'error')

    def test_combat_resolution_is_queued_and_fought_one_battle_at_a_time(self):
        for seed in range(1, 15):
            session = _watch_session(seed)
            messages = _watch(session)[1:]
            for _ in range(80):
                queue = _by_type(messages, 'phase_queue')[0]
                if queue['phase'] == 'COMBAT_RESOLUTION' and queue.get('battle', {}).get('count', 0) >= 2:
                    break
                messages = session.handle_message({'type': 'next'})
            else:
                continue
            count = queue['battle']['count']
            gs = session.engine.game_state
            for i in range(count):
                self.assertEqual((queue['phase'], queue['battle']['index']), ('COMBAT_RESOLUTION', i))
                self.assertEqual(len(queue['events']), 1)
                self.assertEqual(queue['events'][0]['kind'], 'battle_preview')
                messages = session.handle_message({'type': 'next'})
                result = _by_type(messages, 'phase_result')[0]
                summaries = [e for e in result['events'] if e['kind'] == 'battle_summary']
                self.assertEqual(len(summaries), 1)  # exactly this battle
                self.assertEqual(summaries[0]['territory_id'], queue['events'][0]['territory_id'])
                queue = _by_type(messages, 'phase_queue')[0]
            self.assertEqual(queue['phase'], 'NONCOMBAT_MOVE' if queue['phase'] != 'RETURN_TO_BASE' else 'RETURN_TO_BASE')
            self.assertNotIn(queue['phase'], ('COMBAT_RESOLUTION',))
            return
        self.fail('no game in 14 seeds had a turn with 2+ battles')

    def test_a_battle_preview_carries_the_numbers_a_battle_board_places_units_by(self):
        for seed in range(1, 15):
            session = _watch_session(seed)
            messages = _watch(session)[1:]
            for _ in range(80):
                queue = _by_type(messages, 'phase_queue')[0]
                if queue['phase'] == 'COMBAT_RESOLUTION' and queue['events']:
                    break
                messages = session.handle_message({'type': 'next'})
            else:
                continue
            preview = queue['events'][0]
            for row in preview['attackers'] + preview['defenders']:
                for key in ('unit_id', 'unit_type', 'owner', 'side', 'die', 'defense', 'hp', 'max_hp', 'xp', 'promoted', 'promotions', 'cargo'):
                    self.assertIn(key, row)
                if row['cargo']:
                    self.assertEqual((row['die'], row['defense'], row['max_hp']), (None, 6, 1))
            # and the executed battle carries per-round UNIT_STATS events
            result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
            stats = [e for e in result['events'] if e.get('event_kind') == 'UNIT_STATS']
            self.assertTrue(stats)
            self.assertEqual({e['stats_phase'] for e in stats}, {'start', 'end'})
            return
        self.fail('no game in 14 seeds reached a battle')

    def test_aircraft_flying_home_is_its_own_step_before_the_rest_of_non_combat_move(self):
        for seed in range(1, 15):
            session = _watch_session(seed)
            messages = _watch(session)[1:]
            found = False
            for _ in range(60):
                queues = _by_type(messages, 'phase_queue')
                if not queues:
                    break  # this seed's game ended first
                queue = queues[0]
                if queue['phase'] == 'RETURN_TO_BASE':
                    found = True
                    break
                messages = session.handle_message({'type': 'next'})
            if not found:
                continue
            gs = session.engine.game_state
            flights = queue['events'][0]['orders']
            self.assertEqual(queue['events'][0]['kind'], 'return_to_base')
            self.assertEqual(gs.phase, Phase.NONCOMBAT_MOVE)  # still queued, not yet flown
            unit = flights[0]
            in_place = [u.unit_id for u in gs.territories[unit['from']].units]
            self.assertIn(unit['unit_id'], in_place)

            messages = session.handle_message({'type': 'next'})
            result = _by_type(messages, 'phase_result')[0]
            self.assertEqual(result['phase'], 'RETURN_TO_BASE')
            self.assertEqual(result['events'], [dict(queue['events'][0])])
            self.assertEqual(gs.phase, Phase.NONCOMBAT_MOVE)
            self.assertNotIn(unit['unit_id'], [u.unit_id for u in gs.territories[unit['from']].units])
            self.assertIn(unit['unit_id'], [u.unit_id for u in gs.territories[unit['to']].units])
            follow = _by_type(messages, 'phase_queue')[0]
            self.assertEqual(follow['phase'], 'NONCOMBAT_MOVE')
            self.assertNotIn('return_to_base', [e['kind'] for e in follow['events']])
            return
        self.fail('no game in 14 seeds had aircraft returning to base')

    def test_a_faction_eliminated_during_its_own_turn_just_ends_the_turn(self):
        # Capture can hand the active faction's own territory to an ally and
        # drop it to <=1 Strategic Center; every later phase call for it
        # would raise ("not an active faction"), so they must be no-ops.
        session = _watch_session()
        _watch(session)
        for _ in range(3):
            session.handle_message({'type': 'next'})  # Capture is now queued
        session.engine.game_state.factions['NAA'].eliminated = True
        seen = []
        for _ in range(4):
            messages = session.handle_message({'type': 'next'})
            self.assertNotIn('error', [m['type'] for m in messages])
            seen += [m['type'] for m in messages]
            if 'game_over' in seen:
                break
        self.assertIn('game_over', seen)

    def test_a_long_game_steps_cleanly_to_the_end_or_forty_turns(self):
        session = _watch_session(seed=3)
        _watch(session)
        combat_results = 0
        for _ in range(7 * 2 * 40):
            messages = session.handle_message({'type': 'next'})
            self.assertNotIn('error', [m['type'] for m in messages])
            for r in _by_type(messages, 'phase_result'):
                if r['phase'] == 'COMBAT_RESOLUTION' and r['events']:
                    combat_results += 1
            if 'game_over' in [m['type'] for m in messages]:
                break
        self.assertGreater(combat_results, 0)


if __name__ == '__main__':
    unittest.main()


def _human_session(seed=1):
    """NAA a human player, GPC a bot."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['GPC'] = FactionMode.BOT
    gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(seed))
    return GameSession(engine, turn_log, {'GPC': RandomBot(engine, 'GPC', rng=random.Random(seed))})


class TestHumanPurchase(unittest.TestCase):
    def _watch(self):
        session = _human_session()
        messages = _watch(session)
        return session, _by_type(messages, 'phase_queue')[0]

    def test_a_human_purchase_queue_carries_the_options_and_nothing_is_decided_for_them(self):
        session, queue = self._watch()
        self.assertEqual((queue['faction'], queue['phase']), ('NAA', 'PURCHASE'))
        self.assertEqual(queue['events'][0]['orders'], [])
        human = queue['human']
        self.assertEqual(human['treasury'], session.engine.game_state.factions['NAA'].treasury_mpc)
        self.assertEqual(human['total_cost'], 0)
        self.assertEqual(human['orders'], [])
        self.assertTrue(human['targets'])
        for target in human['targets'].values():
            self.assertGreater(target['remaining'], 0)

    def test_staging_replaces_the_queue_and_reports_cost_and_remaining_capacity(self):
        session, queue = self._watch()
        # England (21, a Strategic Center, value 3 -> cap 5): 2 Infantry at the SC price (2 each).
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 21}]})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        human = reply[0]['human']
        self.assertEqual(human['total_cost'], 4)
        self.assertEqual(human['orders'][0]['cost'], 4)
        self.assertEqual(human['targets']['21']['remaining'] if '21' in human['targets'] else human['targets'][21]['remaining'], 3)
        self.assertEqual(reply[0]['events'][0]['orders'], [{'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 21}])
        # nothing has been committed yet
        gs = session.engine.game_state
        self.assertEqual(gs.territories[21].pending_deployment, [])

    def test_restaging_replaces_rather_than_accumulates(self):
        session, _ = self._watch()
        session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 21}]})
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 21}]})
        self.assertEqual(reply[0]['human']['total_cost'], 2)

    def test_an_illegal_stage_is_rejected_with_the_unchanged_queue(self):
        session, queue = self._watch()
        session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 21}]})
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Submarine', 'qty': 1, 'deploy_at': 21}]})  # a ship on land
        self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'])
        self.assertEqual(reply[1]['human']['total_cost'], 2)  # the earlier staging survived

    def test_over_budget_is_rejected(self):
        session, _ = self._watch()
        treasury = session.engine.game_state.factions['NAA'].treasury_mpc
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Armor', 'qty': 40, 'deploy_at': 21}]})
        self.assertEqual(reply[0]['type'], 'error')

    def test_only_a_human_faction_can_stage_and_only_in_its_purchase_phase(self):
        session, _ = self._watch()
        self.assertEqual(session.handle_message({'type': 'stage_purchase', 'faction': 'GPC', 'orders': []})[0]['type'], 'error')
        session.handle_message({'type': 'next'})  # NAA's purchase -> its Combat Resolution
        self.assertEqual(session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': []})[0]['type'], 'error')

    def test_next_commits_the_purchase_and_it_deploys_at_the_deploy_phase(self):
        session, _ = self._watch()
        session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 21}]})
        gs = session.engine.game_state
        before = gs.factions['NAA'].treasury_mpc
        messages = session.handle_message({'type': 'next'})
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual(result['events'][0]['kind'], 'purchase')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, before - 4)
        self.assertEqual(len(gs.territories[21].pending_deployment), 2)
        # play the rest of NAA's turn: the units land at Deploy + Income
        for _ in range(8):
            if gs.active_faction != 'NAA':
                break
            session.handle_message({'type': 'next'})
        self.assertEqual(gs.territories[21].pending_deployment, [])

    def test_a_human_turn_passes_through_every_other_phase_without_orders(self):
        session, _ = self._watch()
        seen = []
        for _ in range(12):
            messages = session.handle_message({'type': 'next'})
            self.assertNotIn('error', [m['type'] for m in messages])
            queue = _by_type(messages, 'phase_queue')[0]
            seen.append((queue['faction'], queue['phase']))
            if queue['faction'] == 'GPC' and queue['phase'] == 'PURCHASE':
                break
        self.assertIn(('NAA', 'DIPLOMACY'), seen)
        self.assertEqual(seen[-2:], [('GPC', 'START_OF_TURN'), ('GPC', 'PURCHASE')])

    def test_purchase_options_list_a_sea_zone_target_with_its_sources(self):
        session, queue = self._watch()
        sea = [(int(t), v) for t, v in queue['human']['targets'].items()
               if session.engine.data.territories()[int(t)]['type'] == 'sea']
        self.assertTrue(sea)
        for tid, info in sea:
            self.assertTrue(info['sources'])


def _human_moves_session(seed=1):
    """NAA human, GPC bot, Combat Move allowed on the first turn (the rules skip it),
    parked at NAA's Combat Move."""
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.HUMAN
    modes['GPC'] = FactionMode.BOT
    gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False, allow_combat_moves_first_turn=True)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(seed))
    session = GameSession(engine, turn_log, {'GPC': RandomBot(engine, 'GPC', rng=random.Random(seed))})
    _watch(session)
    messages = session.handle_message({'type': 'next'})  # NAA's Purchase (nothing bought) -> Combat Move
    return session, _by_type(messages, 'phase_queue')[0]


def _pick_attack(queue):
    """(unit_id, path) for some unit that has a combat-move destination."""
    for uid, opt in queue['human']['options'].items():
        for dest, path in opt['destinations'].items():
            return int(uid), [int(t) for t in path]
    raise AssertionError('no legal combat move at all')


class TestHumanMoves(unittest.TestCase):
    def test_a_human_combat_move_queue_carries_the_legal_options_and_no_staged_moves(self):
        session, queue = _human_moves_session()
        self.assertEqual((queue['faction'], queue['phase']), ('NAA', 'COMBAT_MOVE'))
        human = queue['human']
        self.assertEqual(human['kind'], 'combat')
        self.assertEqual(human['orders'], [])
        self.assertTrue(human['options'])
        for opt in human['options'].values():
            self.assertTrue({'unit_type', 'territory_id', 'destinations'} <= set(opt))

    def test_staging_a_move_queues_it_and_removes_that_unit_from_the_options(self):
        session, queue = _human_moves_session()
        uid, path = _pick_attack(queue)
        reply = session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'path': path}]})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        human = reply[0]['human']
        self.assertEqual([o['unit_id'] for o in human['orders']], [uid])
        self.assertEqual(human['orders'][0]['from'], path[0])
        self.assertEqual(human['orders'][0]['path'], path)
        self.assertNotIn(str(uid), {str(k) for k in human['options']})  # committed units are not available again
        self.assertEqual(reply[0]['events'][0]['orders'][0]['unit_id'], uid)
        # nothing has actually moved yet
        gs = session.engine.game_state
        self.assertIn(uid, [u.unit_id for u in gs.territories[path[0]].units])

    def test_restaging_without_it_recalls_the_move(self):
        session, queue = _human_moves_session()
        uid, path = _pick_attack(queue)
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'path': path}]})
        reply = session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': []})
        self.assertEqual(reply[0]['human']['orders'], [])
        self.assertIn(str(uid), {str(k) for k in reply[0]['human']['options']})

    def test_an_illegal_move_is_rejected_and_the_queue_is_unchanged(self):
        session, queue = _human_moves_session()
        uid, path = _pick_attack(queue)
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'path': path}]})
        reply = session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'path': [path[0], 9999]}]})
        self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'])
        self.assertEqual([o['unit_id'] for o in reply[1]['human']['orders']], [uid])

    def test_next_executes_the_staged_moves(self):
        session, queue = _human_moves_session()
        uid, path = _pick_attack(queue)
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'path': path}]})
        messages = session.handle_message({'type': 'next'})
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual(result['events'][0]['kind'], 'combat_move')
        gs = session.engine.game_state
        self.assertIn(uid, [u.unit_id for u in gs.territories[path[-1]].units] + [u.unit_id for t in gs.territories.values() for u in t.units])
        self.assertNotIn(uid, [u.unit_id for u in gs.territories[path[0]].units])

    def test_only_the_active_human_in_a_move_phase_can_stage(self):
        session, queue = _human_moves_session()
        uid, path = _pick_attack(queue)
        self.assertEqual(session.handle_message({'type': 'stage_moves', 'faction': 'GPC', 'orders': []})[0]['type'], 'error')
        session.handle_message({'type': 'next'})  # NAA's Combat Move -> Combat Resolution
        self.assertEqual(session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': []})[0]['type'], 'error')

    def test_non_combat_move_options_and_staging(self):
        session, queue = _human_moves_session()
        for _ in range(6):
            if queue['phase'] == 'NONCOMBAT_MOVE' and 'human' in queue:
                break
            messages = session.handle_message({'type': 'next'})
            queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'NONCOMBAT_MOVE')
        human = queue['human']
        self.assertEqual(human['kind'], 'noncombat')
        uid, opt = next((int(u), o) for u, o in human['options'].items() if o['destinations'])
        dest = opt['destinations'][0]
        reply = session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': uid, 'destination': dest}]})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        self.assertEqual(reply[0]['human']['orders'][0]['destination'], dest)
        self.assertEqual(reply[0]['human']['orders'][0]['from'], opt['territory_id'])
        result = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]
        self.assertEqual(result['events'][0]['kind'], 'noncombat_move')


def _bombardment_session():
    """NAA human with a Cruiser adjacent to AAC's land Infantry, both HUMAN
    (AAC never needs to act during NAA's own Combat Resolution) -- a direct,
    hand-built state (FakeData), not the real map, for a fully deterministic
    bombardment every time, already parked at NAA's Combat Move."""
    data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
    cruiser = make_unit('Cruiser', 'NAA')
    defender = make_unit('Infantry', 'AAC')
    gs = make_state(
        data, {2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
        units_by_territory={1: [cruiser], 2: [defender]},
    )
    gs.active_faction = 'NAA'
    turn_log = TurnLog()
    engine = GameEngine(gs, data, turn_log=turn_log, combat_rng=random.Random(1))
    session = GameSession(engine, turn_log, {})
    return session, cruiser, defender


class TestBombardmentPacing(unittest.TestCase):
    """rules.json's combat.cruiser_bombardment, over the wire: declaring it
    is an ordinary human combat move; it queues and fires as Combat
    Resolution's own first paced step, strictly before any real battle."""

    def test_declaring_it_is_a_normal_human_combat_move_stage(self):
        session, cruiser, defender = _bombardment_session()
        messages = session.handle_message({'type': 'watch'})
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual((queue['faction'], queue['phase']), ('NAA', 'COMBAT_MOVE'))
        reply = session.handle_message({'type': 'stage_moves', 'faction': 'NAA',
                                         'orders': [{'unit_id': cruiser.unit_id, 'path': [1, 2]}]})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        self.assertEqual(reply[0]['human']['orders'][0]['path'], [1, 2])

    def test_it_queues_and_fires_before_any_battle_would(self):
        session, cruiser, defender = _bombardment_session()
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA',
                                 'orders': [{'unit_id': cruiser.unit_id, 'path': [1, 2]}]})
        messages = session.handle_message({'type': 'next'})  # commits Combat Move -> queues Combat Resolution
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertEqual(queue['bombardment'], {'index': 0, 'count': 1})
        self.assertNotIn('battle', queue)
        self.assertEqual(len(queue['events']), 1)
        preview = queue['events'][0]
        self.assertEqual(preview['kind'], 'bombardment_preview')
        self.assertEqual(preview['territory_id'], 2)
        self.assertEqual(preview['cruiser']['unit_id'], cruiser.unit_id)
        self.assertEqual([d['unit_id'] for d in preview['defenders']], [defender.unit_id])

        messages = session.handle_message({'type': 'next'})  # fights it
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual(result['events'][0]['kind'], 'bombardment')
        self.assertEqual(result['events'][0]['territory_id'], 2)
        queue = _by_type(messages, 'phase_queue')[0]
        # Nothing else was ever declared -- Combat Resolution is immediately done.
        self.assertNotEqual(queue['phase'], 'COMBAT_RESOLUTION')

    def test_the_result_is_logged_in_the_turn_log(self):
        session, cruiser, defender = _bombardment_session()
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA',
                                 'orders': [{'unit_id': cruiser.unit_id, 'path': [1, 2]}]})
        session.handle_message({'type': 'next'})  # queue Combat Resolution
        session.handle_message({'type': 'next'})  # fire the bombardment
        bombardments = [e for e in session.turn_log.events if e['kind'] == 'bombardment']
        self.assertEqual(len(bombardments), 1)
        self.assertEqual(bombardments[0]['territory_id'], 2)
        self.assertEqual(bombardments[0]['cruiser_unit_id'], cruiser.unit_id)

    def test_a_turn_with_no_bombardment_at_all_skips_straight_to_battles(self):
        # Control: an ordinary land attack, no Cruiser involved -- Combat
        # Resolution's queue never mentions 'bombardment' at all.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [attacker], 2: [defender]},
        )
        gs.active_faction = 'NAA'
        turn_log = TurnLog()
        engine = GameEngine(gs, data, turn_log=turn_log, combat_rng=random.Random(1))
        session = GameSession(engine, turn_log, {})
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [{'unit_id': attacker.unit_id, 'path': [1, 2]}]})
        messages = session.handle_message({'type': 'next'})
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertNotIn('bombardment', queue)
        self.assertEqual(queue['events'][0]['kind'], 'battle_preview')

    def test_a_bombardment_and_a_real_battle_the_same_turn_queue_in_order(self):
        # 1 (sea, NAA's Cruiser) -- 2 (land, AAC, Infantry -- bombarded) --
        # 3 (land, AAC, another Infantry, attacked by NAA's own Infantry from 4).
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3]},
        )
        cruiser = make_unit('Cruiser', 'NAA')
        bombarded = make_unit('Infantry', 'AAC')
        attacker = make_unit('Infantry', 'NAA')
        battled = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {2: 'AAC', 3: 'AAC', 4: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [cruiser], 2: [bombarded], 3: [battled], 4: [attacker]},
        )
        gs.active_faction = 'NAA'
        turn_log = TurnLog()
        engine = GameEngine(gs, data, turn_log=turn_log, combat_rng=random.Random(1))
        session = GameSession(engine, turn_log, {})
        session.handle_message({'type': 'watch'})
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [
            {'unit_id': cruiser.unit_id, 'path': [1, 2]}, {'unit_id': attacker.unit_id, 'path': [4, 3]},
        ]})
        messages = session.handle_message({'type': 'next'})  # queues Combat Resolution
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertEqual(queue.get('bombardment'), {'index': 0, 'count': 1}, 'the bombardment queues first')
        self.assertEqual(queue['events'][0]['kind'], 'bombardment_preview')

        messages = session.handle_message({'type': 'next'})  # fires the bombardment
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual([e['kind'] for e in result['events']], ['bombardment'])
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION', 'the real battle is still to come')
        self.assertNotIn('bombardment', queue, 'bombardments are exhausted')
        self.assertEqual(queue['battle'], {'index': 0, 'count': 1})
        self.assertEqual(queue['events'][0]['kind'], 'battle_preview')
        self.assertEqual(queue['events'][0]['territory_id'], 3)

        messages = session.handle_message({'type': 'next'})  # fights the real battle
        result = _by_type(messages, 'phase_result')[0]
        self.assertIn('battle_summary', [e['kind'] for e in result['events']])
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertNotEqual(queue['phase'], 'COMBAT_RESOLUTION', 'both are now done')

    def test_a_bombardment_declared_only_after_an_earlier_bombardment_free_combat_resolution_still_fires(self):
        # PhaseStepper caches self._bombardments per Combat Resolution and only ever
        # recomputes it while it's still None (see _plan_current_phase) -- a turn with
        # NO bombardment declared at all leaves it [] (falsy, but not None), and only
        # _commit_one_bombardment's own exhaustion path used to reset it back to None;
        # _commit_one_battle's own "phase is now done" branch never did, on the theory
        # that _commit's gate always drains any real bombardments before battles are
        # ever reached THAT SAME phase. True within one phase, but this same stepper
        # instance is reused turn after turn -- so a battle-only Combat Resolution
        # (nothing ever calls _commit_one_bombardment at all) left self._bombardments
        # stuck at [] forever, silently skipping declared_bombardments for every
        # faction's every later turn: a Cruiser could legally declare and confirm a
        # bombardment, but Combat Resolution would never see it, log it, or resolve it
        # -- the exact bug report this test is named for.
        #   1 (sea, NAA's Cruiser, held back turn 1) -- 2 (land AAC, Infantry --
        #   bombarded turn 2) -- 3 (land AAC, Infantry, battled turn 1, no Cruiser
        #   involved) -- 4 (land NAA, Infantry attacker, turn 1 only).
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land', 'value': 1}, 3: {'type': 'land', 'value': 1},
                         4: {'type': 'land', 'value': 1}},
            adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3]},
        )
        cruiser = make_unit('Cruiser', 'NAA')
        bombarded = make_unit('Infantry', 'AAC')
        attacker = make_unit('Infantry', 'NAA')
        battled = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {2: 'AAC', 3: 'AAC', 4: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [cruiser], 2: [bombarded], 3: [battled], 4: [attacker]},
        )
        gs.active_faction = 'NAA'
        turn_log = TurnLog()
        engine = GameEngine(gs, data, turn_log=turn_log, combat_rng=random.Random(1))
        session = GameSession(engine, turn_log, {})
        session.handle_message({'type': 'watch'})

        # Turn 1: NAA's Infantry attacks alone -- no Cruiser order at all, so this
        # Combat Resolution never declares a single bombardment.
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [
            {'unit_id': attacker.unit_id, 'path': [4, 3]},
        ]})
        messages = session.handle_message({'type': 'next'})  # queues Combat Resolution
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertNotIn('bombardment', queue, 'nothing declared this turn')
        self.assertEqual(queue['events'][0]['kind'], 'battle_preview')

        # Press through the rest of NAA's turn 1, all of AAC's turn, and into NAA's
        # own Combat Move again -- neither faction has anything left to stage.
        for _ in range(20):
            messages = session.handle_message({'type': 'next'})
            queue = _by_type(messages, 'phase_queue')[0]
            if queue['faction'] == 'NAA' and queue['phase'] == 'COMBAT_MOVE':
                break
        else:
            self.fail('never reached NAA\'s second Combat Move')

        # Turn 2: the same Cruiser, never moved since, now bombards -- and Combat
        # Resolution must still recognize and queue it, not silently skip it.
        session.handle_message({'type': 'stage_moves', 'faction': 'NAA', 'orders': [
            {'unit_id': cruiser.unit_id, 'path': [1, 2]},
        ]})
        messages = session.handle_message({'type': 'next'})  # queues Combat Resolution
        queue = _by_type(messages, 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertEqual(queue.get('bombardment'), {'index': 0, 'count': 1},
                          'the second turn\'s bombardment must still be declared and queued')
        self.assertEqual(queue['events'][0]['kind'], 'bombardment_preview')

        messages = session.handle_message({'type': 'next'})  # fires it
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual([e['kind'] for e in result['events']], ['bombardment'],
                          'the bombardment must actually resolve, not vanish as "nothing happened"')
        self.assertEqual(result['events'][0]['territory_id'], 2)
