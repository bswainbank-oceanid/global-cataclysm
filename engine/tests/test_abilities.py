"""What a unit can do comes from its unit set's abilities, not its name: these tests rename unit
types and move abilities between them, and check the engine follows the data."""
import copy
import random
import unittest

from engine import abilities, data
from engine.combat import resolution_order, resolve_battle
from engine.movement import legal_combat_move_destinations
from engine.state import FactionMode, UnitInstance, max_promotions
from engine.tests.test_movement import FakeData as MoveData, make_state


def units_with(changes):
    """A copy of the real unit set, with {unit_type: {field: value}} changes applied."""
    defs = copy.deepcopy(data.units())
    for name, fields in changes.items():
        defs.setdefault(name, {}).update(fields)
    return defs


def unit(uid, unit_type, owner, defs):
    return UnitInstance(unit_id=uid, unit_type=unit_type, owner=owner, current_hp=defs[unit_type]['hp'])


class TestLookups(unittest.TestCase):
    def test_the_real_unit_set(self):
        defs = data.units()
        self.assertEqual(abilities.unit_types_with(defs, abilities.BOMBARDMENT), ['Cruiser'])
        self.assertEqual(abilities.unit_types_with(defs, abilities.MUSTERING), ['Infantry'])
        self.assertEqual(abilities.transport_unit(defs, 'Mechanized Infantry'), 'Transport')
        self.assertTrue(abilities.triggers_air_superiority(defs, 'Fighter'))
        self.assertFalse(abilities.triggers_air_superiority(defs, 'Bomber'))
        self.assertEqual(abilities.param(defs, 'Aircraft Carrier', abilities.CARRIER_AIR_WING, 'capacity'), 3)

    def test_battle_order_follows_the_unit_fields(self):
        defs = units_with({'Armor': {'land_order': 0}})
        self.assertEqual(resolution_order(defs, 'land')[0], 'Armor')
        self.assertNotIn('Infantry', resolution_order(defs, 'sea'))

    def test_heroic_sets_the_promotion_cap(self):
        defs = units_with({'Armor': {'abilities': {'heroic': {'max_promotions': 7}}}})
        self.assertEqual(max_promotions('Armor', defs), 7)
        self.assertEqual(max_promotions('Infantry', defs), 5)


class TestCombatFollowsTheData(unittest.TestCase):
    def fight(self, attackers, defenders, battle_type, defs, seed=1):
        return list(resolve_battle(attackers, defenders, battle_type, random.Random(seed), 0, defs, data.rules()))

    def test_a_submerging_unit_under_another_name_is_invisible_to_aircraft(self):
        defs = units_with({'Submarine': {'abilities': {}}, 'Cruiser': {'abilities': {'submerge': {}}}})
        cruiser = unit(1, 'Cruiser', 'UE', defs)
        fighters = [unit(10 + i, 'Fighter', 'NAA', defs) for i in range(4)]
        for seed in range(5):
            cruiser.current_hp = defs['Cruiser']['hp']
            events = self.fight(fighters, [cruiser], 'sea', defs, seed)
            self.assertEqual(cruiser.current_hp, defs['Cruiser']['hp'], 'aircraft hit a submerged unit')
            self.assertTrue(events)

    def test_without_a_trigger_there_is_no_air_superiority_round(self):
        defs = units_with({'Fighter': {'abilities': {'air_superiority': {'attack_die': 'D10', 'damage': 3,
                                                                        'triggers_round': False}}}})
        events = self.fight([unit(1, 'Fighter', 'NAA', defs)], [unit(2, 'Fighter', 'UE', defs)], 'land', defs)
        self.assertFalse(any(e.kind.name == 'AIR_SUPERIORITY_START' for e in events))
        events = self.fight([unit(3, 'Fighter', 'NAA', data.units())], [unit(4, 'Fighter', 'UE', data.units())],
                            'land', data.units())
        self.assertTrue(any(e.kind.name == 'AIR_SUPERIORITY_START' for e in events))

    def test_cargo_takes_its_transport_units_stats(self):
        defs = units_with({'Transport': {'hp': 4}})
        mech = unit(1, 'Mechanized Infantry', 'NAA', defs)
        stats = copy.copy(mech)
        stats.in_transport_form = True
        self.assertEqual(stats.effective_stats(defs)['max_hp'], 4)


class TestMovementFollowsTheData(unittest.TestCase):
    def setUp(self):
        # 1 (NAA land) -- 2 (empty UE land) -- 3 (UE land with a defender)
        self.territories = {1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}
        self.adjacency = {1: [2], 2: [1, 3], 3: [2]}

    def reachable(self, defs, unit_type):
        fake = MoveData(self.territories, self.adjacency, unit_defs=defs)
        gs = make_state(fake, {1: 'NAA', 2: 'UE', 3: 'UE'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
                        units_by_territory={1: [unit(1, unit_type, 'NAA', defs)], 3: [unit(2, 'Infantry', 'UE', defs)]})
        return legal_combat_move_destinations(unit_type, 'NAA', 1, gs, fake)

    def test_only_a_blitzing_unit_passes_through_empty_enemy_land(self):
        armor_moves_two = {'Armor': {'combat_move': 2}}
        self.assertEqual(self.reachable(units_with(armor_moves_two), 'Armor'), {2})
        blitz = units_with({'Armor': {'combat_move': 2, 'abilities': {'blitz': {}}}})
        self.assertEqual(self.reachable(blitz, 'Armor'), {2, 3})


if __name__ == '__main__':
    unittest.main()
