"""The scenario rule "Remove units on surrender" (GameState.remove_units_on_surrender, default True). Off: a faction that
surrenders -- forced to, or of its own accord -- takes no more turns and leaves its alliance, but its deployed units
stay on the board and go on defending; only its purchases still waiting to deploy go (GameEngine._eliminate)."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from engine.engine import CombatMoveOrder, GameEngine
from engine.state import FactionMode, GameState, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# A line of land: 1 (NAA's) - 2 (EU's) - 3 (GPC's) - 4 (AAC's).
TERRITORIES = {t: {'type': 'land', 'value': 3, 'name': f'Land {t}'} for t in (1, 2, 3, 4)}
ADJACENCY = {1: [2], 2: [1, 3], 3: [2, 4], 4: [3]}
MODES = {'NAA': FactionMode.BOT, 'EU': FactionMode.HUMAN, 'GPC': FactionMode.BOT, 'AAC': FactionMode.BOT}


def world(remove=False, units=None, phase=Phase.COMBAT_MOVE):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    gs = make_state(data, {1: 'NAA', 2: 'EU', 3: 'GPC', 4: 'AAC'}, MODES, units_by_territory=units or {}, phase=phase)
    gs.remove_units_on_surrender = remove
    gs.active_faction = 'NAA'
    return GameEngine(gs, data), gs


class TestUnitsStay(unittest.TestCase):
    def test_the_rule_is_on_unless_set_off(self):
        self.assertTrue(GameState().remove_units_on_surrender)
        self.assertFalse(GameState.from_dict(dict(world()[1].to_dict())).remove_units_on_surrender)  # (kept in a save)

    def test_with_it_on_the_units_go_as_ever(self):
        engine, gs = world(remove=True, units={2: [make_unit('Infantry', 'EU')]})
        engine.surrender('EU')
        self.assertEqual(gs.territories[2].units, [])

    def test_off_its_units_stay_but_its_turns_and_purchases_and_alliance_go(self):
        infantry = make_unit('Infantry', 'EU')
        engine, gs = world(units={2: [infantry]})
        gs.territories[2].pending_deployment.append(make_unit('Armor', 'EU'))
        gs.factions['EU'].alliance = gs.factions['GPC'].alliance = 'pact'
        engine.surrender('EU')
        self.assertEqual(gs.territories[2].units, [infantry])        # it stays
        self.assertEqual(gs.territories[2].pending_deployment, [])   # what it hadn't deployed goes
        self.assertNotIn('EU', gs.active_factions())                 # no more turns
        self.assertIsNone(gs.factions['EU'].alliance)                # out of its alliance
        self.assertIsNone(gs.factions['GPC'].alliance)               # (an alliance of one is none)
        self.assertEqual(gs.territories[2].owner, 'EU')              # its territory stays as it is

    def test_its_units_go_on_defending(self):
        engine, gs = world(units={1: [make_unit('Armor', 'NAA')], 2: [make_unit('Infantry', 'EU')]})
        engine.surrender('EU')
        [armor] = gs.territories[1].units
        engine.submit_combat_moves('NAA', [CombatMoveOrder(armor.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn('NAA', gs.territories[2].contested_by)
        self.assertIn(2, [tid for tid, _ in engine.declared_battles('NAA')])  # a battle there, not a walk-in

    def test_beside_a_former_ally_it_is_contested(self):
        engine, gs = world(units={3: [make_unit('Infantry', 'GPC'), make_unit('Infantry', 'EU')]})
        gs.factions['EU'].alliance = gs.factions['GPC'].alliance = 'pact'
        engine.surrender('EU')
        self.assertEqual(gs.territories[3].contested_by, {'EU', 'GPC'})

    def test_a_contest_its_units_are_still_in_goes_on(self):
        engine, gs = world(units={2: [make_unit('Infantry', 'EU'), make_unit('Infantry', 'NAA')]})
        gs.territories[2].contested_by = {'EU', 'NAA'}
        engine.surrender('EU')
        self.assertEqual(gs.territories[2].contested_by, {'EU', 'NAA'})
        self.assertEqual(gs.territories[2].owner, 'EU')  # (not handed to NAA: EU's units are still there)


class TestTheBotsSeeThem(unittest.TestCase):
    def test_they_are_defenders_to_beat_but_never_a_threat(self):
        engine, gs = world(units={2: [make_unit('Infantry', 'EU')], 1: [make_unit('Infantry', 'NAA')]},
                           phase=Phase.PURCHASE)
        engine.surrender('EU')
        planner = Planner(engine, 'NAA', load_settings(), 'Strategic', random.Random(1), 'full', 1500)
        self.assertEqual([u.owner for u in planner.enemies_at(2)], ['EU'])
        self.assertNotIn('EU', planner.threats(1))


if __name__ == '__main__':
    unittest.main()
