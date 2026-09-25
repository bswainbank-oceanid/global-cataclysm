import unittest

from engine.state import GameState, TerritoryState
from engine.economy import compute_income


class FakeData:
    def __init__(self, territories):
        self._territories = territories  # {id: {'type', 'value'?, 'strategic_center'?}}

    def territories(self):
        return self._territories

    def sc_bonus(self):
        return 2


class TestComputeIncome(unittest.TestCase):
    def test_sums_value_across_owned_land_territories(self):
        data = FakeData({1: {'type': 'land', 'value': 2}, 2: {'type': 'land', 'value': 3}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner='NAA')
        gs.territories[2] = TerritoryState(territory_id=2, owner='NAA')
        self.assertEqual(compute_income('NAA', gs, data), 5)

    def test_strategic_center_adds_a_flat_2_bonus(self):
        data = FakeData({1: {'type': 'land', 'value': 2, 'strategic_center': True}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner='NAA')
        self.assertEqual(compute_income('NAA', gs, data), 4)

    def test_contested_territory_contributes_nothing(self):
        data = FakeData({1: {'type': 'land', 'value': 5, 'strategic_center': True}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner='NAA', contested_by={'NAA', 'AAC'})
        self.assertEqual(compute_income('NAA', gs, data), 0)

    def test_other_factions_territories_excluded(self):
        data = FakeData({1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 9}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner='NAA')
        gs.territories[2] = TerritoryState(territory_id=2, owner='AAC')
        self.assertEqual(compute_income('NAA', gs, data), 5)

    def test_sea_zones_never_generate_income(self):
        data = FakeData({1: {'type': 'sea'}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner='NAA')
        self.assertEqual(compute_income('NAA', gs, data), 0)

    def test_unowned_territory_contributes_nothing(self):
        data = FakeData({1: {'type': 'land', 'value': 5}})
        gs = GameState()
        gs.territories[1] = TerritoryState(territory_id=1, owner=None)
        self.assertEqual(compute_income('NAA', gs, data), 0)


if __name__ == '__main__':
    unittest.main()
