"""server/report.py: the Game Over report -- one row per seat, sorted by Victory status, Strategic
Centers, Territory MPC, Units produced, Units destroyed."""
import random
import unittest

from engine.bots.random_bot import RandomBot
from engine.bots.strategy_bot import StrategyBot
from engine.engine import GameEngine
from engine.state import FactionMode
from engine.stats import GameStats
from engine.tests.test_engine import FakeData, make_state, make_unit
from engine.turn_log import TurnLog
from server.report import build_game_report


def world(modes, units_by_territory=None):
    # NAA/UE/GPC/AAC/PAF are real faction codes (StrategyBot needs one to draw a style from the real sheet).
    data = FakeData(
        territories={
            1: {'type': 'land', 'value': 5, 'strategic_center': True, 'name': 'NAA Home'},
            2: {'type': 'land', 'value': 3, 'name': 'NAA Extra'},
            3: {'type': 'land', 'value': 5, 'strategic_center': True, 'name': 'UE Home'},
            4: {'type': 'land', 'value': 2, 'name': 'GPC Home'},
            5: {'type': 'land', 'value': 1, 'name': 'AAC Home'},
        },
        adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3, 5], 5: [4]},
    )
    gs = make_state(data, {1: 'NAA', 2: 'NAA', 3: 'UE', 4: 'GPC', 5: 'AAC'}, modes,
                    units_by_territory=units_by_territory)
    engine = GameEngine(gs, data)
    return engine, gs


class TestBuildGameReport(unittest.TestCase):
    def test_a_row_per_seat_with_the_basics(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.NEUTRAL, 'AAC': FactionMode.DEFENSIVE})
        rows = build_game_report(engine, TurnLog(), {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(set(by_fac), {'NAA', 'UE', 'GPC', 'AAC'})
        self.assertEqual(by_fac['NAA']['seat_type'], 'HUMAN')
        self.assertEqual(by_fac['GPC']['seat_type'], 'NEUTRAL')
        self.assertEqual(by_fac['NAA']['strategic_centers'], 1)  # tile 1 -- tile 2 has none
        self.assertEqual(by_fac['UE']['strategic_centers'], 1)

    def test_neutral_and_defensive_seats_have_no_victory_status(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.NEUTRAL, 'AAC': FactionMode.DEFENSIVE})
        rows = build_game_report(engine, TurnLog(), {})
        by_fac = {r['faction']: r for r in rows}
        self.assertIsNone(by_fac['GPC']['victory_status'])
        self.assertIsNone(by_fac['AAC']['victory_status'])
        self.assertIsNotNone(by_fac['NAA']['victory_status'])

    def test_the_last_faction_standing_is_the_winner_and_the_rest_are_out(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.surrender('UE')
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['victory_status'], 'Winner')
        self.assertEqual(by_fac['UE']['victory_status'], 'Surrendered')

    def test_a_forced_surrender_is_told_apart_from_a_self_surrender(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        log.record_surrender(3, 1, 'NAA', 'UE', ['income'])
        gs.factions['UE'].eliminated = True
        log.record_self_surrender(5, 2, 'GPC')
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['UE']['victory_status'], 'Forced to Surrender')
        self.assertEqual(by_fac['GPC']['victory_status'], 'Surrendered')
        self.assertEqual(by_fac['NAA']['victory_status'], 'Winner')

    def test_armistice_participants_are_neither_winners_nor_eliminated(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.end_by_armistice('NAA', ['NAA', 'UE', 'GPC'])
        rows = build_game_report(engine, log, {})
        self.assertTrue(all(r['victory_status'] == 'Armistice' for r in rows if r['faction'] in ('NAA', 'UE', 'GPC')))

    def test_elimination_reason_names_sc_loss_or_economic_or_both(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT, 'AAC': FactionMode.DEFENSIVE})
        log = TurnLog()
        log.record_surrender(3, 1, 'NAA', 'UE', ['income'])
        gs.factions['UE'].eliminated = True
        log.record_surrender(4, 2, 'NAA', 'GPC', ['strategic_center', 'income'])
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['UE']['elimination_reason'], ['Economic'])
        self.assertEqual(by_fac['GPC']['elimination_reason'], ['SC Loss', 'Economic'])  # SC loss named first
        self.assertIsNone(by_fac['NAA']['elimination_reason'])   # still in the game
        self.assertIsNone(by_fac['AAC']['elimination_reason'])   # Defensive, never a competitor

    def test_elimination_reason_for_a_self_surrender(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.surrender('NAA')
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['elimination_reason'], ['Self-Surrender'])

    def test_an_eliminated_proposer_keeps_its_surrender_status_not_armistice(self):
        # An already-out human can still propose an armistice among the survivors (Settings lets them);
        # they aren't relabeled 'Armistice' just for having proposed it -- they're still 'Surrendered'.
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        log.record_self_surrender(2, 1, 'NAA')
        gs.factions['NAA'].eliminated = True
        engine.end_by_armistice('NAA', ['UE', 'GPC'])  # NAA proposes; UE and GPC (the ones still playing) agree
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['victory_status'], 'Surrendered')
        self.assertEqual(by_fac['UE']['victory_status'], 'Armistice')
        self.assertEqual(by_fac['GPC']['victory_status'], 'Armistice')

    def test_territory_mpc_is_the_uncontested_territory_value(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        rows = build_game_report(engine, TurnLog(), {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['territory_mpc'], 5 + 2 + 3)  # tile 1 (5 value + 2 SC bonus) + tile 2 (3)

    def test_units_produced_and_destroyed_come_from_stats(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        stats = GameStats()
        stats.record_deploy('NAA', 'Infantry', 3)
        stats.record_deploy('NAA', 'Armor', 1)
        stats.record_kill('NAA', 'Infantry')
        stats.record_kill('NAA', 'Infantry')
        engine.stats = stats
        rows = build_game_report(engine, TurnLog(), {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['units_produced'], 4)
        self.assertEqual(by_fac['NAA']['units_destroyed'], 2)
        self.assertEqual(by_fac['UE']['units_produced'], 0)

    def test_units_produced_and_destroyed_are_zero_with_no_stats_attached(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        self.assertIsNone(engine.stats)
        rows = build_game_report(engine, TurnLog(), {})
        self.assertTrue(all(r['units_produced'] == 0 and r['units_destroyed'] == 0 for r in rows))

    def test_alliance_history_is_told_in_turn_order(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        log.record_alliance_joined(2, 'NAA', 'UE', 'ALLIANCE_1', new_alliance=True)
        log.record_alliance_joined(4, 'GPC', 'NAA', 'ALLIANCE_1', new_alliance=False)
        log.record_alliance_withdrawal(9, 'NAA', 'ALLIANCE_1', ['NAA', 'UE', 'GPC'])
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['alliance_history'],
                         ['T2: joined with UE', 'T4: joined with GPC', 'T9: left (GPC, UE)'])
        self.assertEqual(by_fac['UE']['alliance_history'], ['T2: joined with NAA', 'T9: NAA left'])
        self.assertEqual(by_fac['GPC']['alliance_history'], ['T4: joined with NAA', 'T9: NAA left'])

    def test_bot_type_and_strategy_come_from_the_actual_bot_instances(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        bots = {
            'UE': RandomBot(engine, 'UE', rng=random.Random(1)),
            'GPC': StrategyBot(engine, 'GPC', rng=random.Random(1)),
        }
        rows = build_game_report(engine, TurnLog(), bots)
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['NAA']['bot_type'], None)
        self.assertIsNone(by_fac['NAA']['bot_strategy'])
        self.assertEqual(by_fac['UE']['bot_type'], 'Random')
        self.assertIsNone(by_fac['UE']['bot_strategy'])
        self.assertEqual(by_fac['GPC']['bot_type'], 'Strategy')
        self.assertEqual(by_fac['GPC']['bot_strategy'], bots['GPC'].base_style)
        self.assertIn(by_fac['GPC']['bot_strategy'], ('Strategic', 'Defensive', 'Expansive', 'Controlling', 'Variable'))

    def test_alliance_strategy_and_behavior_come_from_faction_state(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        gs.factions['UE'].alliance_strategy = 'aggressive'
        gs.factions['UE'].alliance_behavior = 'treacherous'
        rows = build_game_report(engine, TurnLog(), {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual((by_fac['UE']['alliance_strategy'], by_fac['UE']['alliance_behavior']), ('aggressive', 'treacherous'))
        self.assertIsNone(by_fac['NAA']['alliance_strategy'])

    def test_rounds_in_game_is_the_games_current_round_number_on_every_row(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.NEUTRAL})
        gs.round_number = 4
        rows = build_game_report(engine, TurnLog(), {})
        self.assertTrue(all(r['rounds_in_game'] == 4 for r in rows))  # same value on every row, Neutral included

    def test_round_eliminated_comes_from_the_round_number_recorded_at_elimination(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        gs.round_number = 2
        log.record_surrender(3, 2, 'NAA', 'UE', ['income'])
        gs.factions['UE'].eliminated = True
        gs.round_number = 3  # GPC is eliminated one round later
        log.record_self_surrender(7, 3, 'GPC')
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['UE']['round_eliminated'], 2)
        self.assertEqual(by_fac['GPC']['round_eliminated'], 3)
        self.assertIsNone(by_fac['NAA']['round_eliminated'])  # still in the game

    def test_round_eliminated_reflects_the_round_as_it_stood_even_after_later_eliminations_shrink_the_field(self):
        # The whole point of a STORED round_number, rather than deriving one after the fact from
        # global_turn -- an earlier elimination's round must stay correct even once later ones have
        # shrunk active_factions() (see GameState.round_number's own docstring).
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT, 'AAC': FactionMode.DEFENSIVE})
        log = TurnLog()
        engine.turn_log = log
        gs.global_turn = 20
        gs.round_number = 5
        log.record_self_surrender(20, 5, 'UE')
        gs.factions['UE'].eliminated = True
        # The game keeps going and racks up many more turns/rounds after that...
        gs.global_turn = 60
        gs.round_number = 15
        log.record_self_surrender(60, 15, 'GPC')
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['UE']['round_eliminated'], 5)  # NOT recomputed against the final round_number (15)
        self.assertEqual(by_fac['GPC']['round_eliminated'], 15)
        self.assertTrue(all(r['rounds_in_game'] == 15 for r in rows))  # the game's own final length, though

    def test_eliminated_by_names_the_demander_for_a_forced_surrender_and_nobody_for_a_self_surrender(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        log.record_surrender(3, 1, 'NAA', 'UE', ['income'])
        gs.factions['UE'].eliminated = True
        log.record_self_surrender(5, 2, 'GPC')
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        by_fac = {r['faction']: r for r in rows}
        self.assertEqual(by_fac['UE']['eliminated_by'], 'NAA')
        self.assertIsNone(by_fac['GPC']['eliminated_by'])  # self-surrendered: nobody eliminated them but themselves
        self.assertIsNone(by_fac['NAA']['eliminated_by'])  # still in the game

    def test_the_sort_order_is_status_then_scs_then_mpc_then_produced_then_destroyed(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT, 'AAC': FactionMode.DEFENSIVE})
        log = TurnLog()
        log.record_surrender(3, 1, 'NAA', 'GPC', ['income'])
        gs.factions['GPC'].eliminated = True
        rows = build_game_report(engine, log, {})
        # winners (NAA, UE, both still active) first, sorted by SC/MPC among themselves; GPC (eliminated)
        # after them; AAC (Defensive, no status) last.
        statuses = [r['victory_status'] for r in rows]
        self.assertEqual(statuses.index(None), len(statuses) - 1)  # Defensive/Neutral always sort last
        winners = [r for r in rows if r['victory_status'] == 'Winner']
        self.assertEqual([r['faction'] for r in winners], ['NAA', 'UE'])  # NAA has more SC-weighted territory


if __name__ == '__main__':
    unittest.main()
