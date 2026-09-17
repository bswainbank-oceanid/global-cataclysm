import unittest

from engine import data
from engine.setup import build_game_state
from engine.state import PowerMode


def _all_units(gs):
    return [u for t in gs.territories.values() for u in t.units]


class TestSetup(unittest.TestCase):
    def test_200ipc_scenario_unit_counts_match(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario = data.scenario('starting_setup_200ipc')
        for fac in data.factions():
            expected = sum(u['qty'] for entry in scenario['purchases'].get(fac, []) for u in entry['units'])
            actual = sum(1 for u in _all_units(gs) if u.owner == fac)
            self.assertEqual(actual, expected, f'{fac} unit count mismatch')

    def test_naval_units_deploy_to_sea_zones(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        unit_defs = data.units()
        terrs = data.territories()
        for t in gs.territories.values():
            for u in t.units:
                if unit_defs[u.unit_type]['category'] == 'Sea':
                    self.assertEqual(terrs[t.territory_id]['type'], 'sea',
                                      f'{u.unit_type} ({u.owner}) landed on {t.territory_id}, not a sea zone')

    def test_carrier_escort_colocated_with_carrier(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
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
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario = data.scenario('starting_setup_200ipc')
        for fac in data.factions():
            expected = len(scenario['promotions'].get(fac, []))
            actual = sum(1 for u in _all_units(gs) if u.owner == fac and u.promoted)
            self.assertEqual(actual, expected, f'{fac} promotion count mismatch')

    def test_defensive_mode_uses_100ipc_scenario(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        modes['AAC'] = PowerMode.DEFENSIVE
        gs = build_game_state('starting_setup_200ipc', modes)
        scenario_100 = data.scenario('starting_setup_100ipc')
        expected = sum(u['qty'] for entry in scenario_100['purchases'].get('AAC', []) for u in entry['units'])
        actual = sum(1 for u in _all_units(gs) if u.owner == 'AAC')
        self.assertEqual(actual, expected)

    def test_neutral_mode_gets_zero_units(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        modes['PAF'] = PowerMode.NEUTRAL
        gs = build_game_state('starting_setup_200ipc', modes)
        actual = sum(1 for u in _all_units(gs) if u.owner == 'PAF')
        self.assertEqual(actual, 0)

    def test_active_faction_is_first_human_or_bot(self):
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        modes['NAA'] = PowerMode.NEUTRAL
        gs = build_game_state('starting_setup_200ipc', modes)
        self.assertEqual(gs.active_faction, 'UE')
        self.assertNotIn('NAA', gs.active_powers())

    def test_starting_treasury_is_31_mpc_for_every_faction(self):
        # setup.territory_ipc_per_faction (25) + strategic_centers_per_faction
        # (3) x strategic_center_value_bonus (2) = 31 -- confirmed against
        # every faction's real territories.json data, not just the formula.
        modes = {c: PowerMode.HUMAN for c in data.factions()}
        gs = build_game_state('starting_setup_200ipc', modes)
        for fac in data.factions():
            self.assertEqual(gs.factions[fac].treasury_mpc, 31, f'{fac} starting treasury mismatch')


if __name__ == '__main__':
    unittest.main()
