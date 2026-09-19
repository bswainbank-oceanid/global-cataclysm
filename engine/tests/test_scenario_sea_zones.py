import json
import unittest

from engine import data


def _naval_zones(scenario_name):
    """{faction: {sea_zone_id: [(buy_at_territory_id, unit_type), ...]}} exactly as
    engine.setup places them: Sea units (and carrier-escort aircraft) go to the
    override zone for their purchase territory, else that territory's default."""
    scenario = json.load(open(f'data/scenarios/{scenario_name}.json'))
    terrs = data.territories()
    name_to_id = {t['name']: tid for tid, t in terrs.items() if t['type'] == 'sea'}
    overrides = scenario.get('naval_deploy_overrides', {})

    def zone(fac, tid, unit):
        name = overrides.get(fac, {}).get(str(tid), {}).get(unit)
        return name_to_id[name] if name else data.default_sea_zone(tid)

    out = {}
    for fac, entries in scenario['purchases'].items():
        for e in entries:
            for u in e['units']:
                if data.units()[u['unit']]['category'] == 'Sea':
                    out.setdefault(fac, {}).setdefault(zone(fac, e['territory_id'], u['unit']), []).append((e['territory_id'], u['unit']))
        for esc in scenario.get('carrier_escorts', {}).get(fac, []):
            out.setdefault(fac, {}).setdefault(zone(fac, esc['carrier_tid'], 'Aircraft Carrier'), []).append((esc['carrier_tid'], 'escort'))
    return out


SCENARIOS = ['starting_setup_200ipc', 'starting_setup_100ipc']


class TestStartingNavalDeployments(unittest.TestCase):
    def test_every_naval_deployment_is_to_an_adjacent_sea_zone(self):
        adjacency = data.adjacency()
        terrs = data.territories()
        for name in SCENARIOS:
            for fac, zones in _naval_zones(name).items():
                for zone_id, sources in zones.items():
                    self.assertEqual(terrs[zone_id]['type'], 'sea')
                    for tid, unit in sources:
                        self.assertIn(zone_id, adjacency[tid],
                                      f"{name}: {fac}'s {unit} bought at {terrs[tid]['name']} deploys to "
                                      f"{terrs[zone_id]['name']}, which is not adjacent to it")

    def test_no_two_factions_deploy_to_the_same_sea_zone(self):
        # Within one scenario, and ACROSS them: a faction plays the 200-IPC setup as HUMAN/BOT
        # but the 100-IPC one as DEFENSIVE, and any mix can happen in one game.
        used = {}  # zone id -> {faction: scenario}
        for name in SCENARIOS:
            for fac, zones in _naval_zones(name).items():
                for zone_id in zones:
                    used.setdefault(zone_id, {}).setdefault(fac, name)
        terrs = data.territories()
        for zone_id, by_fac in used.items():
            self.assertEqual(len(by_fac), 1, f"{terrs[zone_id]['name']} ({zone_id}) is used by several factions: {by_fac}")

    def test_the_caspian_sea_is_never_used(self):
        for name in SCENARIOS:
            for zones in _naval_zones(name).values():
                self.assertNotIn(43, zones)


if __name__ == '__main__':
    unittest.main()
