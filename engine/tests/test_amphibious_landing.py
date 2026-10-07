"""A land unit crossing an enemy-held sea zone to attack land fights that sea zone's naval battle first, and
only lands -- and fights the land battle -- if it survives (UnitInstance.landing_at,
GameEngine._land_after_sea_battle)."""
import random
import unittest

from engine.engine import CombatMoveOrder, GameEngine
from engine.state import FactionMode, Phase, UnitInstance
from engine.tests.test_engine import FakeData, make_state, make_unit

# 2 (NAA's land) -- 3 (sea, an AAC Cruiser) -- 6 (AAC's land, AAC Infantry)
DATA = FakeData(
    territories={2: {'type': 'land', 'value': 2}, 3: {'type': 'sea'}, 6: {'type': 'land', 'value': 1}},
    adjacency={2: [3], 3: [2, 6], 6: [3]},
)


def setup():
    gs = make_state(DATA, {2: 'NAA', 6: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.BOT},
                    phase=Phase.COMBAT_MOVE)
    mech = make_unit('Mechanized Infantry', 'NAA')
    gs.territories[2].units.append(mech)
    gs.territories[3].units.append(make_unit('Cruiser', 'AAC'))
    gs.territories[6].units.append(make_unit('Infantry', 'AAC'))
    engine = GameEngine(gs, DATA)
    engine.submit_combat_moves('NAA', [CombatMoveOrder(mech.unit_id, [2, 3, 6])])
    engine.confirm_combat_moves('NAA')
    gs.phase = Phase.COMBAT_RESOLUTION
    return engine, gs, mech


def where(gs, unit):
    return next((tid for tid, t in gs.territories.items() if unit in t.units), None)


class TestCrossingHostileWater(unittest.TestCase):
    def test_the_move_stops_in_the_sea_zone_to_fight_there_first(self):
        engine, gs, mech = setup()
        self.assertEqual(where(gs, mech), 3)
        self.assertEqual(mech.landing_at, 6)
        self.assertTrue(mech.arrived_amphibiously)
        self.assertEqual(gs.territories[3].contested_by, {'NAA', 'AAC'})
        self.assertIsNone(gs.territories[6].contested_by)  # (not attacked until it lands)
        self.assertEqual(engine.declared_battles('NAA'), [(3, 'sea'), (6, 'land')])

    def test_a_survivor_lands_after_the_sea_battle_and_the_sunk_never_do(self):
        outcomes = set()
        for seed in range(60):
            engine, gs, mech = setup()
            engine.begin_combat_resolution('NAA')
            engine.resolve_one_battle('NAA', 3, 'sea', random.Random(seed))
            if mech.current_hp > 0 and where(gs, mech) is not None:
                outcomes.add('landed')
                self.assertEqual(where(gs, mech), 6)
                self.assertIsNone(mech.landing_at)
                self.assertIn('NAA', gs.territories[6].contested_by)
                self.assertTrue(engine.battle_ready('NAA', 6))
            else:
                outcomes.add('sunk')
                self.assertIsNone(where(gs, mech))
                self.assertFalse(engine.battle_ready('NAA', 6))  # nobody landed: no land battle
                self.assertIsNone(gs.territories[6].contested_by)
            self.assertNotIn(mech, gs.territories[3].units)
        self.assertEqual(outcomes, {'landed', 'sunk'})  # (both happen across these seeds)

    def test_combat_resolution_fights_the_land_battle_only_after_a_landing(self):
        fought = set()
        for seed in range(30):
            engine, gs, mech = setup()
            results = engine.resolve_combat('NAA', random.Random(seed))
            landed = any(e.get('kind') == 'amphibious_landing' for e in getattr(engine.turn_log, 'events', [])) \
                if engine.turn_log is not None else None
            fought.add(len(results))
            self.assertIn(len(results), (1, 2))  # the sea battle, and the land battle if it landed
            if where(gs, mech) is not None:
                self.assertIsNone(mech.landing_at)  # (a survivor has landed: nothing left waiting)
            if landed is not None:
                self.assertEqual(len(results) == 2, landed)
        self.assertEqual(fought, {1, 2})

    def test_a_waiting_landing_is_saved_with_the_unit(self):
        engine, gs, mech = setup()
        again = UnitInstance.from_dict(mech.to_dict())
        self.assertEqual(again.landing_at, 6)
        plain = make_unit('Infantry', 'NAA')
        self.assertNotIn('landing_at', plain.to_dict())  # (only while one is waiting)


if __name__ == '__main__':
    unittest.main()
