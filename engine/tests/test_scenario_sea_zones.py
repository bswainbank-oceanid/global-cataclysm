import unittest

from engine import data

SETUPS = ['standard', 'defensive']


def _naval_zones(kind):
    """{faction: {sea_zone_id: [(purchased_at, unit_type), ...]}} for every unit a starting setup
    places in a sea zone (ships, and aircraft on their carriers)."""
    setup, _ = data.initial_setup(kind)
    terrs = data.territories()
    out = {}
    for loc in setup['locations']:
        if terrs[loc['location_id']]['type'] != 'sea':
            continue
        for u in loc['units']:
            out.setdefault(u['faction_id'], {}).setdefault(loc['location_id'], []).append(
                (u.get('purchased_at'), u['unit_type_id']))
    return out


class TestStartingNavalDeployments(unittest.TestCase):
    def test_every_naval_deployment_is_to_an_adjacent_sea_zone(self):
        adjacency = data.adjacency()
        terrs = data.territories()
        for kind in SETUPS:
            for fac, zones in _naval_zones(kind).items():
                for zone_id, sources in zones.items():
                    for tid, unit in sources:
                        self.assertIsNotNone(tid, f'{kind}: {fac} {unit} in {zone_id} has no purchased_at')
                        self.assertIn(zone_id, adjacency[tid],
                                      f"{kind}: {fac}'s {unit} bought at {terrs[tid]['name']} deploys to "
                                      f"{terrs[zone_id]['name']}, which is not adjacent to it")

    def test_no_two_factions_deploy_to_the_same_sea_zone(self):
        # Within one setup, and ACROSS them: a faction plays the standard setup as HUMAN/BOT
        # but the defensive one as DEFENSIVE, and any mix can happen in one game.
        used = {}  # zone id -> {faction: setup}
        for kind in SETUPS:
            for fac, zones in _naval_zones(kind).items():
                for zone_id in zones:
                    used.setdefault(zone_id, {}).setdefault(fac, kind)
        terrs = data.territories()
        for zone_id, by_fac in used.items():
            self.assertEqual(len(by_fac), 1, f"{terrs[zone_id]['name']} ({zone_id}) is used by several factions: {by_fac}")

    def test_excluded_sea_zones_are_never_used(self):
        excluded = data.config().naval_deploy_excluded()
        self.assertEqual(excluded, [43])  # the Caspian Sea
        for kind in SETUPS:
            for zones in _naval_zones(kind).values():
                for zone_id in excluded:
                    self.assertNotIn(zone_id, zones)


if __name__ == '__main__':
    unittest.main()
