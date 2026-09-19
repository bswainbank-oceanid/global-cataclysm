import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase
from engine.turn_log import TurnLog
from server.session import GameSession

PHASES = [p.value for p in Phase]


def _watch_session(seed=1):
    modes = {code: FactionMode.NEUTRAL for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
    modes['NAA'] = FactionMode.BOT
    modes['AAC'] = FactionMode.BOT
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(seed))
    bots = {code: RandomBot(engine, code, rng=random.Random(seed)) for code in ('NAA', 'AAC')}
    return GameSession(engine, turn_log, bots)


def _by_type(messages, msg_type):
    return [m for m in messages if m['type'] == msg_type]


class TestWatch(unittest.TestCase):
    def test_watch_answers_with_state_and_the_first_queue(self):
        session = _watch_session()
        messages = session.handle_message({'type': 'watch'})
        self.assertEqual([m['type'] for m in messages], ['state', 'phase_queue'])
        queue = messages[1]
        self.assertEqual((queue['faction'], queue['phase']), ('NAA', 'PURCHASE'))
        self.assertEqual(queue['events'][0]['kind'], 'purchase')

    def test_queued_orders_are_not_executed_until_next(self):
        session = _watch_session()
        session.handle_message({'type': 'watch'})
        gs = session.engine.game_state
        self.assertEqual(session.turn_log.events, [])  # queued, not logged
        treasury = gs.factions['NAA'].treasury_mpc

        messages = session.handle_message({'type': 'next'})
        result = _by_type(messages, 'phase_result')[0]
        self.assertEqual(result['phase'], 'PURCHASE')
        self.assertEqual(result['events'][0]['kind'], 'purchase')
        spent = result['events'][0]['total_cost']
        self.assertEqual(gs.factions['NAA'].treasury_mpc, treasury - spent)

    def test_the_executed_purchase_is_exactly_what_was_queued(self):
        session = _watch_session()
        queued = session.handle_message({'type': 'watch'})[1]['events'][0]
        executed = _by_type(session.handle_message({'type': 'next'}), 'phase_result')[0]['events'][0]
        self.assertEqual(queued, executed)

    def test_each_next_moves_one_phase_and_a_full_turn_hands_over_to_the_next_faction(self):
        session = _watch_session()
        session.handle_message({'type': 'watch'})
        seen = []
        for _ in range(6):
            messages = session.handle_message({'type': 'next'})
            self.assertEqual([m['type'] for m in messages], ['phase_result', 'phase_queue', 'state'])
            queue = messages[1]
            seen.append((queue['faction'], queue['phase']))
        naa_phases = [p for f, p in seen if f == 'NAA']
        aac_phases = [p for f, p in seen if f == 'AAC']
        self.assertEqual(naa_phases, ['COMBAT_RESOLUTION', 'NONCOMBAT_MOVE', 'CAPTURE', 'DEPLOY_INCOME', 'ALLIANCES'])
        self.assertEqual(aac_phases[0], 'PURCHASE')
        self.assertLess(seen.index(('NAA', 'ALLIANCES')), seen.index(('AAC', 'PURCHASE')))

    def test_a_skipped_first_turn_combat_move_is_reported_on_the_next_queue(self):
        session = _watch_session()
        session.handle_message({'type': 'watch'})
        queue = _by_type(session.handle_message({'type': 'next'}), 'phase_queue')[0]
        self.assertEqual(queue['phase'], 'COMBAT_RESOLUTION')
        self.assertEqual(queue['skipped'], ['COMBAT_MOVE'])

    def test_capture_and_deploy_queues_preview_what_executing_them_does(self):
        session = _watch_session()
        session.handle_message({'type': 'watch'})
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
        session.handle_message({'type': 'watch'})
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
        gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
        turn_log = TurnLog()
        engine = GameEngine(gs, None, turn_log=turn_log)
        session = GameSession(engine, turn_log, {'AAC': RandomBot(engine, 'AAC', rng=random.Random(1))})
        self.assertEqual(session.handle_message({'type': 'watch'})[0]['type'], 'error')

    def test_combat_resolution_is_queued_and_fought_one_battle_at_a_time(self):
        for seed in range(1, 15):
            session = _watch_session(seed)
            messages = session.handle_message({'type': 'watch'})[1:]
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
            messages = session.handle_message({'type': 'watch'})[1:]
            for _ in range(80):
                queue = _by_type(messages, 'phase_queue')[0]
                if queue['phase'] == 'COMBAT_RESOLUTION' and queue['events']:
                    break
                messages = session.handle_message({'type': 'next'})
            else:
                continue
            preview = queue['events'][0]
            for row in preview['attackers'] + preview['defenders']:
                for key in ('unit_id', 'unit_type', 'owner', 'side', 'die', 'defense', 'hp', 'max_hp', 'xp', 'promoted', 'cargo'):
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
            messages = session.handle_message({'type': 'watch'})[1:]
            for _ in range(60):
                queue = _by_type(messages, 'phase_queue')[0]
                if queue['phase'] == 'RETURN_TO_BASE':
                    break
                messages = session.handle_message({'type': 'next'})
            else:
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
        session.handle_message({'type': 'watch'})
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
        session.handle_message({'type': 'watch'})
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
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, None, turn_log=turn_log, combat_rng=random.Random(seed))
    return GameSession(engine, turn_log, {'GPC': RandomBot(engine, 'GPC', rng=random.Random(seed))})


class TestHumanPurchase(unittest.TestCase):
    def _watch(self):
        session = _human_session()
        messages = session.handle_message({'type': 'watch'})
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
        # England (21, a Strategic Center, value 3 -> cap 5): 2 Infantry at the SC price (3 each).
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 2, 'deploy_at': 21}]})
        self.assertEqual([m['type'] for m in reply], ['phase_queue'])
        human = reply[0]['human']
        self.assertEqual(human['total_cost'], 6)
        self.assertEqual(human['orders'][0]['cost'], 6)
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
        self.assertEqual(reply[0]['human']['total_cost'], 3)

    def test_an_illegal_stage_is_rejected_with_the_unchanged_queue(self):
        session, queue = self._watch()
        session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Infantry', 'qty': 1, 'deploy_at': 21}]})
        reply = session.handle_message({'type': 'stage_purchase', 'faction': 'NAA', 'orders': [
            {'unit_type': 'Submarine', 'qty': 1, 'deploy_at': 21}]})  # a ship on land
        self.assertEqual([m['type'] for m in reply], ['error', 'phase_queue'])
        self.assertEqual(reply[1]['human']['total_cost'], 3)  # the earlier staging survived

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
        self.assertEqual(gs.factions['NAA'].treasury_mpc, before - 6)
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
            if queue['faction'] == 'GPC':
                break
        self.assertIn(('NAA', 'ALLIANCES'), seen)
        self.assertEqual(seen[-1], ('GPC', 'PURCHASE'))

    def test_purchase_options_list_a_sea_zone_target_with_its_sources(self):
        session, queue = self._watch()
        sea = [(int(t), v) for t, v in queue['human']['targets'].items()
               if session.engine.data.territories()[int(t)]['type'] == 'sea']
        self.assertTrue(sea)
        for tid, info in sea:
            self.assertTrue(info['sources'])
