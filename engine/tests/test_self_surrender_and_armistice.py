"""GameEngine.surrender (the Settings 'Surrender' action) and end_by_armistice (the Settings
'Propose Armistice' action, once accepted) -- both out-of-band, not gated to any phase."""
import unittest

from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state


def world(modes, phase=Phase.COMBAT_MOVE):
    """Three factions, one land territory each, at some ordinary mid-turn phase -- self-surrender and
    armistice must work regardless of whose turn or which phase it is."""
    data = FakeData(
        territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'land', 'value': 3}},
        adjacency={1: [2], 2: [1, 3], 3: [2]},
    )
    gs = make_state(data, {1: 'NAA', 2: 'UE', 3: 'GPC'}, modes, phase=phase)
    gs.active_faction = 'UE'  # deliberately NOT the faction under test, below
    return GameEngine(gs, data), gs


class TestSelfSurrender(unittest.TestCase):
    def test_it_works_at_any_phase_regardless_of_whose_turn_it_is(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')  # NAA's own turn isn't even active -- UE's is
        self.assertTrue(gs.factions['NAA'].eliminated)
        self.assertNotIn('NAA', gs.active_factions())

    def test_it_removes_units_and_pending_purchases_and_cuts_alliance_ties(self):
        from engine.tests.test_engine import make_unit
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        gs.territories[2].pending_deployment.append(make_unit('Infantry', 'NAA'))
        gs.factions['NAA'].alliance = gs.factions['UE'].alliance = 'pact'
        engine.surrender('NAA')
        self.assertEqual(gs.territories[1].units, [])
        self.assertEqual(gs.territories[2].pending_deployment, [])
        self.assertIsNone(gs.factions['NAA'].alliance)
        self.assertIsNone(gs.factions['UE'].alliance)  # an alliance of one is no alliance

    def test_the_territory_stays_as_it_is(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')

    def test_it_logs_a_self_surrender_then_an_elimination(self):
        from engine.turn_log import TurnLog
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.surrender('NAA')
        self.assertEqual([e['kind'] for e in log.events], ['self_surrender', 'faction_eliminated'])
        self.assertEqual(log.events[0]['faction'], 'NAA')

    def test_it_ends_the_game_immediately_if_one_faction_is_left(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertTrue(gs.game_over)
        self.assertEqual(gs.active_factions(), ['UE'])

    def test_it_does_not_end_the_game_with_two_or_more_unallied_factions_left(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertFalse(gs.game_over)

    def test_it_ends_the_game_if_the_remaining_two_are_mutually_allied(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gs.factions['UE'].alliance = gs.factions['GPC'].alliance = 'pact'
        engine.surrender('NAA')
        self.assertTrue(gs.game_over)

    def test_cannot_surrender_twice_or_after_the_game_is_over(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('UE')  # the game is over

    def test_a_neutral_or_defensive_faction_cannot_surrender(self):
        engine, gs = world({'NAA': FactionMode.NEUTRAL, 'UE': FactionMode.DEFENSIVE, 'GPC': FactionMode.BOT})
        with self.assertRaises(ValueError):
            engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('UE')

    def test_no_such_faction_is_rejected(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        with self.assertRaises(ValueError):
            engine.surrender('XYZ')


class TestArmistice(unittest.TestCase):
    def test_it_ends_the_game_and_logs_who_agreed(self):
        from engine.turn_log import TurnLog
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.end_by_armistice('NAA', ['NAA', 'UE', 'GPC'])
        self.assertTrue(gs.game_over)
        self.assertEqual(log.events[-1], {'kind': 'armistice', 'turn': 0, 'faction': 'NAA', 'participants': ['GPC', 'NAA', 'UE']})

    def test_nobody_is_eliminated_by_an_armistice(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.end_by_armistice('NAA', ['NAA', 'UE', 'GPC'])
        self.assertEqual(sorted(gs.active_factions()), ['GPC', 'NAA', 'UE'])
        self.assertFalse(any(f.eliminated for f in gs.factions.values()))

    def test_it_works_at_any_phase(self):
        for phase in (Phase.PURCHASE, Phase.COMBAT_RESOLUTION, Phase.DIPLOMACY):
            engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT, 'GPC': FactionMode.BOT}, phase=phase)
            engine.end_by_armistice('NAA', ['NAA', 'UE', 'GPC'])
            self.assertTrue(gs.game_over, phase)

    def test_cannot_end_an_already_over_game_by_armistice(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'UE': FactionMode.BOT})
        engine.surrender('NAA')  # ends the game (last one standing)
        with self.assertRaises(ValueError):
            engine.end_by_armistice('UE', ['UE'])


if __name__ == '__main__':
    unittest.main()
