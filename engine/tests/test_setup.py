import random
import unittest

from engine import data
from engine.setup import build_game_state
from engine.state import FactionMode


def _all_units(gs):
    return [u for t in gs.territories.values() for u in t.units]


class TestSetup(unittest.TestCase):
    def test_200ipc_scenario_unit_counts_match(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario = data.scenario('starting_setup_200ipc')
        for fac in data.factions():
            expected = sum(u['qty'] for entry in scenario['purchases'].get(fac, []) for u in entry['units'])
            actual = sum(1 for u in _all_units(gs) if u.owner == fac)
            self.assertEqual(actual, expected, f'{fac} unit count mismatch')

    def test_naval_units_deploy_to_sea_zones(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        unit_defs = data.units()
        terrs = data.territories()
        for t in gs.territories.values():
            for u in t.units:
                if unit_defs[u.unit_type]['category'] == 'Sea':
                    self.assertEqual(terrs[t.territory_id]['type'], 'sea',
                                      f'{u.unit_type} ({u.owner}) landed on {t.territory_id}, not a sea zone')

    def test_carrier_escort_colocated_with_carrier(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario = data.scenario('starting_setup_200ipc')
        for fac, escorts in scenario['carrier_escorts'].items():
            carrier_zones = {t.territory_id for t in gs.territories.values()
                              for u in t.units if u.owner == fac and u.unit_type == 'Aircraft Carrier'}
            for esc in escorts:
                escort_present = any(
                    u.owner == fac and u.unit_type == esc['unit']
                    for tid in carrier_zones for u in gs.territories[tid].units
                )
                self.assertTrue(escort_present, f'{fac} escort {esc} not found alongside its carrier')

    def test_promotions_applied(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario = data.scenario('starting_setup_200ipc')
        for fac in data.factions():
            expected = len(scenario['promotions'].get(fac, []))
            actual = sum(1 for u in _all_units(gs) if u.owner == fac and u.promoted)
            self.assertEqual(actual, expected, f'{fac} promotion count mismatch')

    def test_defensive_mode_uses_100ipc_scenario(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['AAC'] = FactionMode.DEFENSIVE
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario_100 = data.scenario('starting_setup_100ipc')
        expected = sum(u['qty'] for entry in scenario_100['purchases'].get('AAC', []) for u in entry['units'])
        actual = sum(1 for u in _all_units(gs) if u.owner == 'AAC')
        self.assertEqual(actual, expected)

    def test_neutral_mode_gets_zero_units(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['PAF'] = FactionMode.NEUTRAL
        gs = build_game_state('starting_setup_200ipc', modes)
        actual = sum(1 for u in _all_units(gs) if u.owner == 'PAF')
        self.assertEqual(actual, 0)

    def test_active_faction_is_first_human_or_bot(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.NEUTRAL
        # randomize_play_order defaults True (game_start_settings) --
        # disable it here since this test checks a specific, deterministic
        # first-mover, not the randomization itself.
        gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
        self.assertEqual(gs.active_faction, 'UE')
        self.assertNotIn('NAA', gs.active_factions())

    def test_starting_treasury_is_31_mpc_for_every_faction(self):
        # setup.territory_ipc_per_faction (25) + strategic_centers_per_faction
        # (3) x strategic_center_value_bonus (2) = 31 -- confirmed against
        # every faction's real territories.json data, not just the formula.
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        for fac in data.factions():
            self.assertEqual(gs.factions[fac].treasury_mpc, 31, f'{fac} starting treasury mismatch')

    def test_randomize_play_order_shuffles_active_factions(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        unshuffled = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False).active_factions()
        # A fixed seed makes this deterministic -- just needs to differ
        # from the unshuffled (JSON insertion) order to prove the shuffle
        # actually ran, not any particular resulting order.
        shuffled = build_game_state(
            'starting_setup_200ipc', modes, randomize_play_order=True, rng=random.Random(1),
        ).active_factions()
        self.assertEqual(set(shuffled), set(unshuffled))
        self.assertNotEqual(shuffled, unshuffled)

    def test_randomize_play_order_false_keeps_json_insertion_order(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
        self.assertEqual(gs.active_factions(), list(data.factions()))

    def test_game_start_settings_default_to_no_combat_moves_yes_noncombat_moves_first_turn(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        self.assertFalse(gs.allow_combat_moves_first_turn)
        self.assertTrue(gs.allow_noncombat_moves_first_turn)

    def test_variable_alliance_settings_get_an_initial_concrete_reroll(self):
        # A 'variable' strategy/behavior gets its first concrete pick
        # right away at setup too, not just at the bot's own first
        # Purchase phase (RandomBot._maybe_reroll_variable_alliance_
        # settings) -- so there's a real decision from turn 1, not None,
        # e.g. if another bot invites it before its own first turn.
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.BOT
        gs = build_game_state(
            'starting_setup_200ipc', modes,
            alliance_strategies={'NAA': 'variable'}, alliance_behaviors={'NAA': 'variable'},
            rng=random.Random(1),
        )
        self.assertEqual(gs.factions['NAA'].alliance_strategy, 'variable', 'stays variable all game')
        self.assertEqual(gs.factions['NAA'].alliance_behavior, 'variable')
        self.assertIsNotNone(gs.factions['NAA'].current_alliance_strategy)
        self.assertIsNotNone(gs.factions['NAA'].current_alliance_behavior)
        self.assertNotEqual(gs.factions['NAA'].current_alliance_strategy, 'variable')
        self.assertNotEqual(gs.factions['NAA'].current_alliance_behavior, 'variable')

    def test_non_variable_alliance_settings_leave_current_fields_unset(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.BOT
        gs = build_game_state(
            'starting_setup_200ipc', modes,
            alliance_strategies={'NAA': 'aggressive'}, alliance_behaviors={'NAA': 'loyal'},
            rng=random.Random(1),
        )
        self.assertIsNone(gs.factions['NAA'].current_alliance_strategy)
        self.assertIsNone(gs.factions['NAA'].current_alliance_behavior)


if __name__ == '__main__':
    unittest.main()
