import unittest

from engine.state import GameState, TerritoryState, FactionState, UnitInstance, PowerMode
from engine.movement import (
    legal_combat_move_destinations, legal_noncombat_move_destinations, legal_air_move_destinations,
)

# Minimal unit_defs -- movement.py only ever reads combat_move/
# non_combat_move/category. No 'Transport' entry: it's never
# independently movement-queried (see engine/movement.py's module
# docstring) -- a land unit crossing water just gets the +1 bonus,
# there's no separate Transport-as-a-unit move.
LAND_UNITS = {
    'Infantry': {'category': 'Land', 'combat_move': 1, 'non_combat_move': 2},
    'Mechanized Infantry': {'category': 'Land', 'combat_move': 2, 'non_combat_move': 2},
    'Armor': {'category': 'Land', 'combat_move': 1, 'non_combat_move': 2},
    'Fighter': {'category': 'Air', 'combat_move': 2, 'non_combat_move': 3},
    'Bomber': {'category': 'Air', 'combat_move': 3, 'non_combat_move': 3},
    'Submarine': {'category': 'Sea', 'combat_move': 2, 'non_combat_move': 2},
    'Aircraft Carrier': {'category': 'Sea', 'combat_move': 2, 'non_combat_move': 2},
}


class FakeData:
    """A stand-in for engine.data with a hand-built, fully controlled
    territory graph -- the real 149-territory map isn't practical for
    hand-verifying an exact multi-hop reachability set."""
    def __init__(self, territories, adjacency, unit_defs=None):
        self._territories = territories  # {id: {'type': 'land'|'sea'}}
        self._adjacency = adjacency  # {id: [neighbor ids]}
        self._units = unit_defs or LAND_UNITS

    def units(self):
        return self._units

    def territories(self):
        return self._territories

    def adjacency(self):
        return self._adjacency


def make_state(data, territory_owners, faction_modes, contested=None, units_by_territory=None):
    """data: the FakeData for this test, used to get every territory id
    in the map -- a TerritoryState is created for ALL of them (sea zones
    included), not just the ones with an explicit owner, since movement.py
    looks up game_state.territories[id] for any territory it visits.
    territory_owners: {id: faction_code}, only for owned (land) ones.
    faction_modes: {faction_code: PowerMode}. contested: {id: {faction_codes}}.
    units_by_territory: {id: [UnitInstance, ...]}."""
    gs = GameState()
    for code, mode in faction_modes.items():
        gs.factions[code] = FactionState(code=code, mode=mode)
    for tid in data.territories():
        gs.territories[tid] = TerritoryState(
            territory_id=tid, owner=territory_owners.get(tid),
            units=(units_by_territory or {}).get(tid, []),
            contested_by=(contested or {}).get(tid),
        )
    return gs


def enemy_unit(uid, unit_type, owner):
    return UnitInstance(unit_id=uid, unit_type=unit_type, owner=owner, current_hp=1)


class TestWaterMovementBonus(unittest.TestCase):
    def test_infantry_can_cross_water_and_continue_onto_land(self):
        # 1 (land, origin) -- 2 (sea) -- 3 (land, empty enemy -- a legal
        # combat-move stop). Infantry's base combat_move is 1; without
        # the water bonus it could only reach territory 2.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(3, dest)

    def test_mech_inf_starting_in_water_moves_three_spaces(self):
        # 1 (sea, origin) -- 2 (land, friendly) -- 3 (land, friendly) --
        # 4 (land, empty enemy) -- 5 (land, empty enemy, one hop too far).
        # Mech Inf: base 2 + water bonus (already in water) = 3.
        data = FakeData(
            territories={i: {'type': 'sea' if i == 1 else 'land'} for i in range(1, 6)},
            adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3, 5], 5: [4]},
        )
        gs = make_state(
            data,
            territory_owners={2: 'NAA', 3: 'NAA', 4: 'AAC', 5: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertIn(4, dest)
        self.assertNotIn(5, dest, 'budget is exactly 3 (2 base + 1 water bonus) -- territory 5 is a 4th hop')


class TestMechInfEmptyTerritoryPassThrough(unittest.TestCase):
    def _setup(self, unit_defs=None):
        # 1 (land, origin) -- 2 (land, empty enemy) -- 3 (land, empty enemy, beyond 2)
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
            unit_defs=unit_defs,
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
        )
        return data, gs

    def test_mech_inf_passes_through_and_captures_empty_territory(self):
        data, gs = self._setup()
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertIn(3, dest)

    def test_non_mech_inf_unit_cannot_chain_past_empty_foreign_territory(self):
        # Give a non-Mech-Inf unit the same 2-move budget via custom
        # stats, to isolate "the rule forbids it" from "ran out of moves".
        custom = dict(LAND_UNITS, Armor={'category': 'Land', 'combat_move': 2, 'non_combat_move': 2})
        data, gs = self._setup(unit_defs=custom)
        dest = legal_combat_move_destinations('Armor', 'NAA', 1, gs, data)
        self.assertIn(2, dest)  # can still capture the first one
        self.assertNotIn(3, dest, 'only Mechanized Infantry may pass through an empty foreign territory')


class TestOccupiedTerritoryStopsMovement(unittest.TestCase):
    def test_occupied_foreign_territory_blocks_further_chaining_even_for_mech_inf(self):
        # 1 (land, origin) -- 2 (land, enemy-occupied) -- 3 (land, beyond 2)
        custom = dict(LAND_UNITS, **{'Mechanized Infantry': {'category': 'Land', 'combat_move': 3, 'non_combat_move': 2}})
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
            unit_defs=custom,
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(99, 'Infantry', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertNotIn(3, dest, 'an occupied foreign territory stops the move even for Mech Inf')

    def test_transport_does_not_count_as_occupying(self):
        # 1 (sea, origin) -- 2 (sea, contains only an enemy Transport) --
        # 3 (sea, an actual warship to attack -- empty open sea is never
        # a legal combat-move STOP in its own right, so a real target is
        # needed to prove passage through 2 wasn't blocked).
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Transport', 'AAC')], 3: [enemy_unit(2, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertIn(3, dest, 'a Transport alone should not block passage through its sea zone')


class TestAmphibiousThroughOccupiedWater(unittest.TestCase):
    def test_land_unit_can_fight_through_occupied_water_onto_adjacent_land(self):
        # 1 (land, origin) -- 2 (sea, enemy warship present) -- 3 (land, beyond)
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)  # must be prepared to fight the naval battle there
        self.assertIn(3, dest)  # and can land on the far side in the same move

    def test_land_unit_cannot_chain_from_occupied_water_into_more_open_sea(self):
        # 1 (land, origin) -- 2 (sea, enemy warship) -- 3 (sea, open, beyond)
        custom = dict(LAND_UNITS, Infantry={'category': 'Land', 'combat_move': 3, 'non_combat_move': 2})
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
            unit_defs=custom,
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertNotIn(3, dest, 'the through-occupied-water exception reaches land only, never more open sea')

    def test_real_naval_unit_does_not_get_the_land_unit_exception(self):
        # A Submarine has no "land it can reach" concept -- occupied
        # water should stop it like the general rule, even with land
        # adjacent, since it isn't in transit as cargo.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertNotIn(3, dest, 'a real naval unit has no amphibious-landing exception')

    def test_land_unit_can_escape_through_contested_water_onto_friendly_land(self):
        # 1 (land, origin) -- 2 (sea, CONTESTED, no enemy units actually
        # present -- an unresolved battle, not an occupying force) -- 3
        # (land, friendly -- a safe landing, not an attack). Still a
        # combat move, and a legal stop, even though 3 is the mover's
        # own uncontested territory (which wouldn't normally be a combat
        # -move stop on its own).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 3: 'NAA'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            contested={2: {'NAA'}},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertIn(3, dest, 'friendly land is a legal landing spot when escaping contested water')


class TestNeutralExclusion(unittest.TestCase):
    def test_neutral_territory_is_never_reachable_and_blocks_passage(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'PAF', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'PAF': PowerMode.NEUTRAL, 'AAC': PowerMode.HUMAN},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertNotIn(2, dest)
        self.assertNotIn(3, dest, 'Neutral territory blocks passage entirely, not just capture')


class TestNonCombatMoveDestinations(unittest.TestCase):
    def test_friendly_and_self_contested_are_legal_clean_foreign_is_not(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2, 3, 4]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA', 3: 'AAC', 4: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            contested={3: {'NAA'}},
        )
        dest = legal_noncombat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)  # friendly
        self.assertIn(3, dest)  # contested by the mover
        self.assertNotIn(4, dest)  # clean foreign -- never legal for a non-combat move


class TestAirMovement(unittest.TestCase):
    def test_air_flies_over_occupied_territory_freely(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': PowerMode.HUMAN, 'AAC': PowerMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Armor', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertIn(3, dest, 'air units are exempt from the enemy-occupation stop rule')

    def test_noncombat_air_landing_restricted_to_friendly_territory_or_own_carrier(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}, 4: {'type': 'sea'}},
            adjacency={1: [2, 3, 4]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA', 3: 'NAA'},
            faction_modes={'NAA': PowerMode.HUMAN},
            contested={3: {'NAA'}},  # contested LAND -- not a legal air landing spot
            units_by_territory={4: [enemy_unit(1, 'Aircraft Carrier', 'NAA')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertIn(2, dest)  # friendly land
        self.assertIn(4, dest)  # own carrier's sea zone
        self.assertNotIn(3, dest, 'a contested LAND territory is not a legal landing spot for aircraft')


if __name__ == '__main__':
    unittest.main()
