import unittest

from engine import data as real_data
from engine.bots.driver import play_to_completion
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import Phase, PowerMode
from engine.stats import GameStats
from engine.tests.test_engine import FakeData, make_state, make_unit


class TestPurchaseTargetPools(unittest.TestCase):
    def test_sc_adjacent_sea_zone_is_classified_as_an_sc_target(self):
        # 1: land SC (NAA), 2: land non-SC (NAA), 3: sea adjacent to both.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 2, 'strategic_center': True},
                2: {'type': 'land', 'value': 3},
                3: {'type': 'sea'},
            },
            adjacency={1: [3], 2: [3], 3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        sc_targets, other_targets = bot._purchase_target_pools()
        self.assertEqual(set(sc_targets), {1, 3})
        self.assertEqual(set(other_targets), {2})

    def test_sea_zone_with_no_adjacent_sc_is_not_an_sc_target(self):
        data = FakeData(
            territories={2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={2: [3], 3: [2]},
        )
        gs = make_state(data, {2: 'NAA'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        sc_targets, other_targets = bot._purchase_target_pools()
        self.assertEqual(sc_targets, [])
        self.assertEqual(set(other_targets), {2, 3})


class TestPurchasePhase(unittest.TestCase):
    def test_spends_within_treasury_and_deploys_pending_units(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT}, treasury={'NAA': 20})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA', rng=__import__('random').Random(1))
        bot.take_purchase_phase()

        self.assertGreaterEqual(gs.factions['NAA'].treasury_mpc, 0)
        self.assertLessEqual(gs.factions['NAA'].treasury_mpc, 20)
        pending_cost = sum(
            data.units()[u.unit_type]['sc_cost'] for u in gs.territories[1].pending_deployment
        )
        self.assertEqual(pending_cost, 20 - gs.factions['NAA'].treasury_mpc)


class TestCombatMovePhase(unittest.TestCase):
    def test_prefers_lowest_id_among_equally_close_targets(self):
        # 2 (NAA) adjacent to 4 and 7, both empty AAC-owned land, 1 hop away.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                4: {'type': 'land', 'value': 1},
                7: {'type': 'land', 'value': 1},
            },
            adjacency={2: [4, 7], 4: [2], 7: [2]},
        )
        gs = make_state(data, {2: 'NAA', 4: 'AAC', 7: 'AAC'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT},
                        phase=Phase.COMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[4].units])
        self.assertTrue(unit.has_moved_combat)

    def test_transported_land_unit_prefers_land_over_stopping_at_sea(self):
        # 2 (NAA infantry) -> 3 (sea, hostile: AAC Cruiser present) -> 6 (empty foreign land).
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                3: {'type': 'sea'},
                6: {'type': 'land', 'value': 1},
            },
            adjacency={2: [3], 3: [2, 6], 6: [3]},
        )
        gs = make_state(data, {2: 'NAA'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT}, phase=Phase.COMBAT_MOVE)
        mover = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(mover)
        gs.territories[3].units.append(make_unit('Cruiser', 'AAC'))
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(mover.unit_id, [u.unit_id for u in gs.territories[6].units])


class TestNonCombatMovePhase(unittest.TestCase):
    def test_moves_toward_nearest_enemy_owned_territory(self):
        # 2 (NAA unit) -- 8 (NAA, empty) -- 9 (AAC-owned): only 8 is a
        # legal non-combat destination (9 is clean foreign land), and
        # it's the one that gets closer to 9.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                8: {'type': 'land', 'value': 2},
                9: {'type': 'land', 'value': 1},
            },
            adjacency={2: [8], 8: [2, 9], 9: [8]},
        )
        gs = make_state(data, {2: 'NAA', 8: 'NAA', 9: 'AAC'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT},
                        phase=Phase.NONCOMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_noncombat_move_phase()

        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[8].units])
        self.assertTrue(unit.has_moved_noncombat)

    def test_unit_with_no_legal_moves_stays_put(self):
        data = FakeData(territories={2: {'type': 'land', 'value': 2}}, adjacency={2: []})
        gs = make_state(data, {2: 'NAA'}, {'NAA': PowerMode.BOT, 'AAC': PowerMode.BOT}, phase=Phase.NONCOMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_noncombat_move_phase()  # should not raise
        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[2].units])


class TestPlayToCompletion(unittest.TestCase):
    def test_naa_vs_aac_bot_game_runs_without_error(self):
        modes = {code: PowerMode.NEUTRAL for code in real_data.factions()}
        modes['NAA'] = PowerMode.BOT
        modes['AAC'] = PowerMode.BOT
        gs = build_game_state('starting_setup_200ipc', modes)
        stats = GameStats()
        engine = GameEngine(gs, stats=stats)
        bots = {
            'NAA': RandomBot(engine, 'NAA', rng=__import__('random').Random(1)),
            'AAC': RandomBot(engine, 'AAC', rng=__import__('random').Random(2)),
        }

        turns_played = play_to_completion(engine, bots, max_turns=15)

        self.assertGreater(turns_played, 0)
        report = stats.report()
        self.assertIn('=== Territory Captures ===', report)
        self.assertIn('=== Unit Stats ===', report)


if __name__ == '__main__':
    unittest.main()
