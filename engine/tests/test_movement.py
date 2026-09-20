import random
import unittest

from engine.state import GameState, TerritoryState, FactionState, UnitInstance, FactionMode
from engine.movement import (
    legal_combat_move_destinations, legal_combat_move_paths, legal_noncombat_move_destinations,
    legal_air_move_destinations, find_emergency_landing, trace_combat_move,
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


def make_state(data, territory_owners, faction_modes, contested=None, units_by_territory=None, pending_deployment_by_territory=None):
    """data: the FakeData for this test, used to get every territory id
    in the map -- a TerritoryState is created for ALL of them (sea zones
    included), not just the ones with an explicit owner, since movement.py
    looks up game_state.territories[id] for any territory it visits.
    territory_owners: {id: faction_code}, only for owned (land) ones.
    faction_modes: {faction_code: FactionMode}. contested: {id: {faction_codes}}.
    units_by_territory: {id: [UnitInstance, ...]}.
    pending_deployment_by_territory: {id: [UnitInstance, ...]} -- units
    bought this turn's purchase phase, not yet actually on the board."""
    gs = GameState()
    for code, mode in faction_modes.items():
        gs.factions[code] = FactionState(code=code, mode=mode)
    for tid in data.territories():
        gs.territories[tid] = TerritoryState(
            territory_id=tid, owner=territory_owners.get(tid),
            units=(units_by_territory or {}).get(tid, []),
            contested_by=(contested or {}).get(tid),
            pending_deployment=(pending_deployment_by_territory or {}).get(tid, []),
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Transport', 'AAC')], 3: [enemy_unit(2, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertIn(3, dest, 'a Transport alone should not block passage through its sea zone')


class TestEnemyTransportsDoNotBlock(unittest.TestCase):
    """A land unit afloat IS a Transport (no separate unit type): an enemy one never
    blocks a non-combat move or forces a combat move to stop, though it can still be attacked."""

    def setUp(self):
        # 1 -- 2 (an enemy Infantry afloat) -- 3 (a warship), all sea.
        self.data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        self.modes = {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}

    def state(self, warship=True):
        units = {2: [enemy_unit(1, 'Infantry', 'AAC')]}
        if warship:
            units[3] = [enemy_unit(2, 'Cruiser', 'AAC')]
        return make_state(self.data, {}, self.modes, units_by_territory=units)

    def test_a_combat_move_passes_through_afloat_land_units(self):
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, self.state(), self.data)
        self.assertIn(3, dest)

    def test_the_transports_themselves_can_be_attacked(self):
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, self.state(warship=False), self.data)
        self.assertIn(2, dest)

    def test_a_non_combat_move_is_not_blocked(self):
        dest = legal_noncombat_move_destinations('Submarine', 'NAA', 1, self.state(), self.data)
        self.assertIn(2, dest)
        self.assertNotIn(3, dest)  # 3 holds a warship

    def test_a_warship_still_blocks_a_non_combat_move(self):
        gs = make_state(self.data, {}, self.modes, units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]})
        self.assertNotIn(2, legal_noncombat_move_destinations('Submarine', 'NAA', 1, gs, self.data))

    def test_planes_can_attack_afloat_transports(self):
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', self.state(warship=False), self.data)
        self.assertIn(2, dest)


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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
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
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'NAA'}},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)
        self.assertIn(3, dest, 'friendly land is a legal landing spot when escaping contested water')

    def test_amphibious_landing_is_never_continuable_even_onto_empty_foreign_land(self):
        # Mech Inf: combat_move 2, +1 for touching water = 3 -- budget
        # alone would allow a 3rd hop onto territory 4 (empty foreign
        # land, which Mech Inf could normally blitz through) -- but a
        # landing via the hostile-water exception must be the final stop
        # of the move regardless, so 4 must NOT appear as a legal
        # destination (matches trace_combat_move's own rule -- a bug
        # found and fixed this session: _reachable_destinations used to
        # keep exploring past such a landing whenever the landing spot's
        # own ordinary classification permitted it).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'NAA', 4: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'NAA', 'AAC'}},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertIn(3, dest, 'the landing itself is still a legal stop')
        self.assertNotIn(4, dest, 'cannot continue past an amphibious landing, even onto empty foreign land')

    def test_reconstructed_path_survives_trace_combat_move_even_with_a_cheaper_alternate_route(self):
        # A destination (4, own land) reachable BOTH via a genuine
        # amphibious landing (1 -> 5 -> 2(hostile) -> 4, 3 hops) AND via
        # a cheaper, merely-pass-through route that never itself
        # qualifies as a stop (1 -> 3(open) -> 4, 2 hops, own land is
        # always pass-only on its own merits). The BFS's budget-only
        # pruning (see _reachable_destinations' docstring) means the
        # cheaper route can arrive at 4 with a strictly better remaining
        # budget than the qualifying one -- a bug found and fixed this
        # session: legal_combat_move_paths used to reconstruct 4's route
        # via whichever visit had the best remaining budget, REGARDLESS
        # of whether that visit was the one that actually justified
        # adding 4 to the destination set, so it could hand back a path
        # trace_combat_move would then reject as illegal. Adjacency order
        # here ([3, 5] before the hostile branch is even reached)
        # deliberately lets the stack (LIFO) explore the hostile route
        # first and the cheaper one second, so the overwrite would have
        # been live if the bug were still present.
        data = FakeData(
            territories={
                1: {'type': 'land'}, 5: {'type': 'land'}, 2: {'type': 'sea'},
                3: {'type': 'sea'}, 4: {'type': 'land'},
            },
            adjacency={1: [3, 5], 5: [1, 2], 2: [5, 4], 3: [1, 4], 4: [2, 3]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 5: 'NAA', 4: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertIn(4, dest)
        paths = legal_combat_move_paths('Mechanized Infantry', 'NAA', 1, gs, data)
        # Whatever path was recorded for 4 must actually be legal --
        # this is the assertion that would have caught the bug.
        trace_combat_move('Mechanized Infantry', 'NAA', paths[4], gs, data)


class TestNeutralExclusion(unittest.TestCase):
    def test_neutral_territory_is_never_reachable_and_blocks_passage(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'PAF', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN},
        )
        dest = legal_combat_move_destinations('Mechanized Infantry', 'NAA', 1, gs, data)
        self.assertNotIn(2, dest)
        self.assertNotIn(3, dest, 'Neutral territory blocks passage entirely, not just capture')

    def test_amphibious_escape_cannot_land_on_neutral_territory(self):
        # 1 (land, NAA origin) -- 2 (sea, hostile: enemy Cruiser present)
        # -- 3 (land, NEUTRAL). The hostile-water escape/amphibious
        # exception overrides ordinary land classification (own/ally
        # land's normal pass-only status), but must never override
        # NEUTRAL exclusion -- a bug found and fixed this session
        # (`hop.stop or (land_only and neighbor_is_land)` used to bypass
        # _is_neutral entirely).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 3: 'PAF'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)  # still must be prepared to fight the naval battle there
        self.assertNotIn(3, dest, 'neutral territory is never a legal landing spot, even via the amphibious exception')


class TestSeaUnitsStayAtSea(unittest.TestCase):
    """A sea unit can never enter, cross, or attack land, in either move
    phase -- previously nothing stopped it, so bot ships wound up in (and
    fought from) land territories."""

    def test_combat_move_never_ends_on_enemy_land(self):
        # 1 (sea, origin) -- 2 (land, AAC, occupied) and 3 (sea, enemy
        # Cruiser). The occupied land would be a plain "attack" for any
        # other unit type; for a sea unit only the enemy-occupied WATER is.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3], 2: [1], 3: [1]},
        )
        gs = make_state(
            data, territory_owners={2: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Infantry', 'AAC')], 3: [enemy_unit(2, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertIn(3, dest)
        self.assertNotIn(2, dest, 'a sea unit cannot attack land')

    def test_combat_move_cannot_pass_through_friendly_land(self):
        # 1 (sea) -- 2 (land, NAA's own) -- 3 (sea, enemy Cruiser): only
        # reachable by crossing the land, which a sea unit can't do.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={2: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={3: [enemy_unit(1, 'Cruiser', 'AAC')]},
        )
        dest = legal_combat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertNotIn(3, dest, 'land is not a bridge between two sea zones')
        self.assertNotIn(2, dest)

    def test_noncombat_move_never_ends_on_or_crosses_friendly_land(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(data, territory_owners={2: 'NAA'}, faction_modes={'NAA': FactionMode.HUMAN})
        dest = legal_noncombat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertNotIn(2, dest, 'a sea unit cannot move onto friendly land')
        self.assertNotIn(3, dest, 'nor across it')

    def test_sea_units_still_move_between_sea_zones(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(data, territory_owners={}, faction_modes={'NAA': FactionMode.HUMAN})
        dest = legal_noncombat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertEqual(dest, {2, 3})

    def test_trace_combat_move_rejects_a_sea_unit_path_onto_land(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}},
            adjacency={1: [2], 2: [1]},
        )
        gs = make_state(
            data, territory_owners={2: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Infantry', 'AAC')]},
        )
        with self.assertRaisesRegex(ValueError, 'sea unit'):
            trace_combat_move('Submarine', 'NAA', [1, 2], gs, data)

    def test_land_units_are_unaffected(self):
        # Control: an Infantry next to the same occupied land can still attack it.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Infantry', 'AAC')]},
        )
        self.assertIn(2, legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data))


class TestNonCombatMoveDestinations(unittest.TestCase):
    def test_friendly_and_self_contested_are_legal_clean_foreign_is_not(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2, 3, 4]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA', 3: 'AAC', 4: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={3: {'NAA'}},
        )
        dest = legal_noncombat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest)  # friendly
        self.assertIn(3, dest)  # contested by the mover
        self.assertNotIn(4, dest)  # clean foreign -- never legal for a non-combat move

    def test_any_contested_territory_is_legal_regardless_of_participants(self):
        # 1 (land, origin) -- 2 (land, owned by AAC, contested by a
        # THIRD faction, UER -- the mover, NAA, isn't involved at all).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UER': FactionMode.HUMAN},
            contested={2: {'UER'}},
        )
        dest = legal_noncombat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest, 'a contested territory is a legal non-combat destination regardless of who is contesting it')


class TestAllianceAwareMovement(unittest.TestCase):
    def _allied_state(self, data, territory_owners, contested=None, units_by_territory=None):
        gs = make_state(
            data, territory_owners,
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested=contested, units_by_territory=units_by_territory,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        return gs

    def test_noncombat_move_into_allied_land_is_legal(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = self._allied_state(data, {1: 'NAA', 2: 'UE'})
        dest = legal_noncombat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(2, dest, "moving into an ally's territory is a legal non-combat move")

    def test_combat_move_can_pass_through_allied_land(self):
        # 1 (land, origin, NAA) -- 2 (land, allied UE, uncontested) -- 3
        # (land, empty enemy AAC, beyond 2). Confirms allied territory is
        # freely transitable for a combat move too, not just non-combat.
        custom = dict(LAND_UNITS, Infantry={'category': 'Land', 'combat_move': 2, 'non_combat_move': 2})
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
            unit_defs=custom,
        )
        gs = self._allied_state(data, {1: 'NAA', 2: 'UE', 3: 'AAC'})
        dest = legal_combat_move_destinations('Infantry', 'NAA', 1, gs, data)
        self.assertIn(3, dest, "allied territory should be freely passable on a combat move, same as your own")

    def test_ally_occupied_sea_zone_does_not_block_noncombat_movement(self):
        # 1 (sea, origin) -- 2 (sea, only an ALLY's warship present) --
        # 3 (sea, beyond). Should pass freely; an ally isn't an enemy.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = self._allied_state(data, {}, units_by_territory={2: [enemy_unit(1, 'Cruiser', 'UE')]})
        dest = legal_noncombat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertIn(3, dest, "an ally's warship should not block non-combat passage through its sea zone")

    def test_non_ally_occupied_sea_zone_blocks_noncombat_movement(self):
        # Same shape, but the occupying faction (AAC) is NOT an ally.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = self._allied_state(data, {}, units_by_territory={2: [enemy_unit(1, 'Cruiser', 'AAC')]})
        dest = legal_noncombat_move_destinations('Submarine', 'NAA', 1, gs, data)
        self.assertNotIn(3, dest, 'a non-ally-occupied sea zone should block non-combat passage -- would need to be a combat move')


class TestAirMovement(unittest.TestCase):
    def test_air_flies_over_occupied_territory_to_reach_a_further_attack_target(self):
        # 2 is occupied (would stop a ground unit's combat move); 3 is a
        # separate, further attack target. Confirms both that air isn't
        # blocked by occupation in transit AND that the actual
        # destination still has to be a legitimate attack target.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Armor', 'AAC')], 3: [enemy_unit(2, 'Armor', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertIn(3, dest, 'air units are exempt from the enemy-occupation stop rule')

    def test_air_combat_move_cannot_end_on_empty_or_friendly_territory(self):
        # Air alone can't capture, so an undefended foreign territory
        # isn't a legal attack target; landing on your own territory via
        # a combat move isn't an attack either.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertNotIn(2, dest, 'own territory is not an attack target')
        self.assertNotIn(3, dest, "air can't capture, so empty foreign territory isn't a legal combat-move target")

    def test_noncombat_air_landing_on_own_land_allowed_even_if_contested(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}, 4: {'type': 'sea'}},
            adjacency={1: [2, 3, 4]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA', 3: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN},
            contested={3: {'NAA'}},  # own contested land -- IS a legal air landing spot
            units_by_territory={4: [enemy_unit(1, 'Aircraft Carrier', 'NAA')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertIn(2, dest)  # friendly land
        self.assertIn(3, dest, 'contested land you own is a legal landing spot for aircraft')
        self.assertIn(4, dest)  # own carrier's sea zone

    def test_noncombat_air_landing_on_ally_land_legal_even_if_contested(self):
        # Confirmed this session: allied land is a legal landing spot
        # regardless of contested status -- own contested land is fine
        # too, naturally, since it's the mover's own. The real
        # restriction lives on the sea/carrier side (see below), never
        # on land.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'UE'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            contested={2: {'NAA'}},  # ally's contested territory
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertIn(2, dest, "allied land is a legal landing spot even contested -- landing doesn't care")

    def test_noncombat_air_landing_still_requires_own_carrier_not_allys(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Aircraft Carrier', 'UE')]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertNotIn(2, dest, "an ally's carrier is still not a legal landing target, only your own")

    def test_noncombat_air_landing_on_truly_open_water_is_illegal(self):
        # No carrier of any kind at territory 2, and none pending --
        # genuinely open water. Landing here is NOT legal to declare,
        # even though a carrier COULD arrive later the same phase via
        # its own move order: movement.py evaluates one unit's move in
        # isolation and can't (and per this rule, shouldn't) anticipate
        # another unit's not-yet-submitted move.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertNotIn(2, dest, 'open water with no carrier and none pending must not be a legal landing spot')

    def test_noncombat_air_landing_legal_when_own_carrier_pending_deployment(self):
        # No carrier physically at territory 2 yet, but the mover's own
        # faction has one queued in this turn's pending_deployment --
        # a real, already-known GameState fact (unlike another unit's
        # own move order), so this IS legal to declare.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN},
            pending_deployment_by_territory={2: [enemy_unit(9, 'Aircraft Carrier', 'NAA')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertIn(2, dest, "a zone with the mover's own carrier queued to deploy this turn is a legal landing spot")

    def test_noncombat_air_landing_illegal_when_only_allied_carrier_pending_deployment(self):
        # Same "own carrier only" restriction applies to a PENDING
        # deployment too, not just one already on the board -- an
        # ally's queued carrier doesn't make this a legal landing spot.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            pending_deployment_by_territory={2: [enemy_unit(9, 'Aircraft Carrier', 'UE')]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertNotIn(2, dest, "an ally's carrier, pending or not, is still never a legal landing spot")

    def test_air_flies_over_neutral_territory_to_reach_a_further_attack_target(self):
        # 1 (land, NAA origin) -- 2 (land, NEUTRAL) -- 3 (land, AAC,
        # occupied -- a legal attack target). Confirmed: neutral
        # territory blocks land/sea entirely (TestNeutralExclusion) but
        # air may now fly straight over it.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'PAF', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN},
            units_by_territory={3: [enemy_unit(1, 'Armor', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertIn(3, dest, 'air can fly over neutral territory to reach a further attack target')
        self.assertNotIn(2, dest, 'neutral territory is still never itself a legal attack target')

    def test_air_noncombat_move_can_pass_through_neutral_territory(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'PAF', 3: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertIn(3, dest, 'air can fly through neutral territory to reach a further friendly landing spot')
        self.assertNotIn(2, dest, 'neutral territory is still never a legal landing spot, even though flyover is allowed')

    def test_enemy_fighter_in_transit_forces_a_combat_move_to_stop_and_attack_there(self):
        # 1 (land, NAA origin) -- 2 (land, AAC, an enemy FIGHTER present)
        # -- 3 (land, AAC, occupied by Armor, a further attack target).
        # Ordinarily air would fly straight over 2 to reach 3 (see
        # test_air_flies_over_occupied_territory_to_reach_a_further_
        # attack_target) -- an enemy Fighter specifically disrupts that.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Fighter', 'AAC')], 3: [enemy_unit(2, 'Armor', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertIn(2, dest, 'an enemy Fighter present forces the move to stop there and attack')
        self.assertNotIn(3, dest, 'cannot continue past a territory holding an enemy Fighter in the same move')

    def test_enemy_bomber_in_transit_does_not_disrupt_a_combat_move(self):
        # Same shape as above, but the occupant at 2 is a Bomber, not a
        # Fighter -- per this session's rule, only a Fighter disrupts
        # overflight; a Bomber is an ordinary occupant, same as Armor.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Bomber', 'AAC')], 3: [enemy_unit(2, 'Armor', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'combat', gs, data)
        self.assertIn(3, dest, 'an enemy Bomber, unlike a Fighter, never disrupts overflight')

    def test_enemy_fighter_blocks_noncombat_pass_through_entirely(self):
        # 1 (land, NAA origin) -- 2 (land, AAC, an enemy Fighter present)
        # -- 3 (land, NAA, friendly -- would ordinarily be a legal
        # landing spot). The Fighter makes 2 fully off limits: neither a
        # landing spot nor a pass-through hop, so 3 becomes unreachable.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'AAC', 3: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Fighter', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertNotIn(2, dest, 'an enemy Fighter makes that territory entirely off limits for a non-combat move')
        self.assertNotIn(3, dest, 'cannot pass through a Fighter-held territory to reach a further landing spot')

    def test_enemy_fighter_overrides_the_own_contested_land_landing_allowance(self):
        # Contrast with test_noncombat_air_landing_on_own_land_allowed_
        # even_if_contested: own contested land is normally a fine
        # landing spot regardless of who's contesting it, but an enemy
        # FIGHTER specifically contesting it blocks landing there too.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data,
            territory_owners={1: 'NAA', 2: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'AAC'}},
            units_by_territory={2: [enemy_unit(1, 'Fighter', 'AAC')]},
        )
        dest = legal_air_move_destinations('Fighter', 'NAA', 1, 'noncombat', gs, data)
        self.assertNotIn(2, dest, 'an enemy Fighter overrides the usual own-contested-land landing allowance')


class TestFindEmergencyLanding(unittest.TestCase):
    """find_emergency_landing is a pure one-hop spatial query -- WHEN it's
    appropriate to call it (only at battle end, only for the defender) is
    entirely engine.py's job and isn't exercised here."""

    def test_prefers_own_carrier_over_own_or_allied_land(self):
        # 1 (sea, origin, carrier just destroyed) -- 2 (own land),
        # 3 (own carrier's sea zone), 4 (allied land).
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}, 4: {'type': 'land'}},
            adjacency={1: [2, 3, 4]},
        )
        gs = make_state(
            data,
            territory_owners={2: 'NAA', 4: 'UE'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            units_by_territory={3: [enemy_unit(1, 'Aircraft Carrier', 'NAA')]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        result = find_emergency_landing(1, 'NAA', gs, data, random.Random(0))
        self.assertEqual(result, 3)

    def test_falls_back_to_own_land_when_no_own_carrier_adjacent(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data,
            territory_owners={2: 'NAA', 3: 'UE'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        result = find_emergency_landing(1, 'NAA', gs, data, random.Random(0))
        self.assertEqual(result, 2, 'own land beats allied land')

    def test_falls_back_to_allied_land_when_nothing_better_adjacent(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data,
            territory_owners={2: 'UE', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        result = find_emergency_landing(1, 'NAA', gs, data, random.Random(0))
        self.assertEqual(result, 2, 'allied land is the only qualifying option; enemy land 3 must be excluded')

    def test_allied_carrier_does_not_count_as_a_safe_landing(self):
        # Unlike the voluntary noncombat-move-declaration rule, an ally's
        # carrier is NOT a valid emergency landing spot -- only the
        # mover's own carrier is. With no own carrier, own land, or
        # allied land adjacent, this must return None even though an
        # ally's carrier sits right next door.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, territory_owners={},
            faction_modes={'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Aircraft Carrier', 'UE')]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        result = find_emergency_landing(1, 'NAA', gs, data, random.Random(0))
        self.assertIsNone(result, "an ally's carrier is not a safe home for a forced emergency landing")

    def test_returns_none_when_nothing_qualifies(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, territory_owners={2: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        result = find_emergency_landing(1, 'NAA', gs, data, random.Random(0))
        self.assertIsNone(result, 'enemy land is never a qualifying emergency landing spot')

    def test_neutral_territory_is_excluded(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, territory_owners={2: 'NAA'},
            faction_modes={'NAA': FactionMode.NEUTRAL},
        )
        # territory 2 is 'owned' by NAA but NAA is itself the Neutral
        # faction here -- exercising the exclusion path directly regardless
        # of who the mover is.
        result = find_emergency_landing(1, 'AAC', gs, data, random.Random(0))
        self.assertIsNone(result)

    def test_random_choice_within_a_tier(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, territory_owners={2: 'NAA', 3: 'NAA'},
            faction_modes={'NAA': FactionMode.HUMAN},
        )
        seen = {find_emergency_landing(1, 'NAA', gs, data, random.Random(seed)) for seed in range(20)}
        self.assertEqual(seen, {2, 3}, 'both own-land options should be reachable across enough random seeds')


class TestTraceCombatMove(unittest.TestCase):
    def test_single_hop_attack(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Infantry', 'AAC')]},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2], gs, data)
        self.assertEqual(trace.final_kind, 'attack')
        self.assertEqual(trace.entered_en_route, [])

    def test_crossed_water_false_for_a_pure_overland_path(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Infantry', 'AAC')]},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2], gs, data)
        self.assertFalse(trace.crossed_water)

    def test_crossed_water_true_when_the_path_passes_through_a_sea_zone(self):
        # 1 (land, origin) -- 2 (sea) -- 3 (land, empty enemy).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2, 3], gs, data)
        self.assertTrue(trace.crossed_water)

    def test_crossed_water_true_when_already_starting_in_a_sea_zone(self):
        # 1 (sea, origin -- e.g. stranded there from an earlier turn) -- 2 (land, empty enemy).
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={2: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2], gs, data)
        self.assertTrue(trace.crossed_water)

    def test_crossed_water_always_false_for_a_sea_unit(self):
        # empty open sea is never a legal final stop -- use an
        # enemy-occupied sea zone as the destination instead.
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={2: [enemy_unit(1, 'Submarine', 'AAC')]},
        )
        trace = trace_combat_move('Submarine', 'NAA', [1, 2], gs, data)
        self.assertFalse(trace.crossed_water, 'crossing open water is just normal movement for a sea unit, not a "crossing"')

    def test_single_hop_capture_of_empty_foreign_land(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        trace = trace_combat_move('Armor', 'NAA', [1, 2], gs, data)
        self.assertEqual(trace.final_kind, 'capture')

    def test_mech_inf_blitz_captures_intermediate_empty_territories_then_attacks(self):
        # Mech Inf's combat_move is 2 -- a 2-hop path: capture the empty
        # territory 2 along the way, then attack the defended territory 3.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            units_by_territory={3: [enemy_unit(1, 'Infantry', 'AAC')]},
        )
        trace = trace_combat_move('Mechanized Infantry', 'NAA', [1, 2, 3], gs, data)
        self.assertEqual(trace.entered_en_route, [2])
        self.assertEqual(trace.final_kind, 'attack')

    def test_mech_inf_blitz_ends_in_capture_not_attack(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        trace = trace_combat_move('Mechanized Infantry', 'NAA', [1, 2, 3], gs, data)
        self.assertEqual(trace.entered_en_route, [2])
        self.assertEqual(trace.final_kind, 'capture')

    def test_non_mech_inf_cannot_pass_through_empty_foreign_land(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        with self.assertRaises(ValueError):
            trace_combat_move('Armor', 'NAA', [1, 2, 3], gs, data)

    def test_path_exceeding_budget_is_rejected(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'NAA', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        # Infantry combat_move is 1 -- can't legally traverse two hops.
        with self.assertRaises(ValueError):
            trace_combat_move('Infantry', 'NAA', [1, 2, 3], gs, data)

    def test_non_adjacent_hop_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
        )
        with self.assertRaises(ValueError):
            trace_combat_move('Mechanized Infantry', 'NAA', [1, 3], gs, data)

    def test_joining_an_already_contested_destination(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'AAC'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'AAC', 'UE'}},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2], gs, data)
        self.assertEqual(trace.final_kind, 'join_contest')

    def test_amphibious_escape_through_hostile_water_to_friendly_land(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'NAA'}, faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'NAA', 'AAC'}},
        )
        trace = trace_combat_move('Infantry', 'NAA', [1, 2, 3], gs, data)
        self.assertEqual(trace.final_kind, 'safe_landing')

    def test_cannot_continue_past_an_amphibious_landing(self):
        # Mech Inf: combat_move 2, +1 for touching water = 3, so budget
        # alone would allow a 3rd hop -- but landing via the hostile-
        # water exception must be the final stop of the move regardless.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2, 4], 4: [3]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'NAA', 4: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            contested={2: {'NAA', 'AAC'}},
        )
        with self.assertRaises(ValueError):
            trace_combat_move('Mechanized Infantry', 'NAA', [1, 2, 3, 4], gs, data)

    def test_neutral_territory_blocks_the_path(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 2: 'PAF', 3: 'AAC'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN},
        )
        with self.assertRaises(ValueError):
            trace_combat_move('Mechanized Infantry', 'NAA', [1, 2, 3], gs, data)

    def test_amphibious_escape_cannot_land_on_neutral_territory(self):
        # Same hostile-water-escape shape as
        # test_amphibious_escape_through_hostile_water_to_friendly_land,
        # but the far shore is NEUTRAL -- must still be rejected, even
        # though the amphibious exception otherwise skips the landing
        # hop's ordinary classification entirely.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        gs = make_state(
            data, territory_owners={1: 'NAA', 3: 'PAF'},
            faction_modes={'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN},
            contested={2: {'NAA', 'AAC'}},
        )
        with self.assertRaises(ValueError):
            trace_combat_move('Infantry', 'NAA', [1, 2, 3], gs, data)


if __name__ == '__main__':
    unittest.main()
