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
    gs = make_state(data, {1: 'NAA', 2: 'EU', 3: 'GPC'}, modes, phase=phase)
    gs.active_faction = 'EU'  # deliberately NOT the faction under test, below
    gs.remove_units_on_surrender = True  # (the scenario rule on: a surrendering faction's units leave the board)
    return GameEngine(gs, data), gs


class TestSelfSurrender(unittest.TestCase):
    def test_it_works_at_any_phase_regardless_of_whose_turn_it_is(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')  # NAA's own turn isn't even active -- EU's is
        self.assertTrue(gs.factions['NAA'].eliminated)
        self.assertNotIn('NAA', gs.active_factions())

    def test_on_its_own_turn_the_turn_ends_and_the_next_in_order_begins(self):
        engine, gs = world({'NAA': FactionMode.BOT, 'EU': FactionMode.HUMAN, 'GPC': FactionMode.BOT})
        round_before, turn_before = gs.round_number, gs.global_turn
        engine.surrender('EU')  # EU's own Combat Move: NAA, EU, GPC is the order
        self.assertEqual((gs.active_faction, gs.phase), ('GPC', Phase.PURCHASE))
        self.assertEqual((gs.round_number, gs.global_turn), (round_before, turn_before + 1))  # (no lap skipped)

    def test_the_last_in_order_giving_up_on_its_turn_starts_the_next_round(self):
        engine, gs = world({'NAA': FactionMode.BOT, 'EU': FactionMode.BOT, 'GPC': FactionMode.HUMAN})
        gs.active_faction = 'GPC'
        round_before = gs.round_number
        engine.surrender('GPC')
        self.assertEqual((gs.active_faction, gs.round_number), ('NAA', round_before + 1))

    def test_it_removes_units_and_pending_purchases_and_cuts_alliance_ties(self):
        from engine.tests.test_engine import make_unit
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        gs.territories[2].pending_deployment.append(make_unit('Infantry', 'NAA'))
        gs.factions['NAA'].alliance = gs.factions['EU'].alliance = 'pact'
        engine.surrender('NAA')
        self.assertEqual(gs.territories[1].units, [])
        self.assertEqual(gs.territories[2].pending_deployment, [])
        self.assertIsNone(gs.factions['NAA'].alliance)
        self.assertIsNone(gs.factions['EU'].alliance)  # an alliance of one is no alliance

    def test_the_territory_stays_as_it_is(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')

    def test_it_logs_a_self_surrender_then_an_elimination(self):
        from engine.turn_log import TurnLog
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.surrender('NAA')
        self.assertEqual([e['kind'] for e in log.events], ['self_surrender', 'faction_eliminated'])
        self.assertEqual(log.events[0]['faction'], 'NAA')

    def test_it_ends_the_game_immediately_if_one_faction_is_left(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertTrue(gs.game_over)
        self.assertEqual(gs.active_factions(), ['EU'])

    def test_it_does_not_end_the_game_with_two_or_more_unallied_factions_left(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.surrender('NAA')
        self.assertFalse(gs.game_over)

    def test_it_ends_the_game_if_the_remaining_two_are_mutually_allied(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gs.factions['EU'].alliance = gs.factions['GPC'].alliance = 'pact'
        engine.surrender('NAA')
        self.assertTrue(gs.game_over)

    def test_cannot_surrender_twice_or_after_the_game_is_over(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT})
        engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('EU')  # the game is over

    def test_a_noncombatant_or_neutral_faction_cannot_surrender(self):
        engine, gs = world({'NAA': FactionMode.NONCOMBATANT, 'EU': FactionMode.NEUTRAL, 'GPC': FactionMode.BOT})
        with self.assertRaises(ValueError):
            engine.surrender('NAA')
        with self.assertRaises(ValueError):
            engine.surrender('EU')

    def test_no_such_faction_is_rejected(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        with self.assertRaises(ValueError):
            engine.surrender('XYZ')


class TestEliminationSettlesContests(unittest.TestCase):
    """victory.elimination_settles_contests: a contest the eliminated faction was part of is settled at once --
    a territory left with a single faction in its contest goes to it now, not at its next Capture Territory."""

    def board(self, units, contested, owners=None):
        from engine.turn_log import TurnLog
        data = FakeData(
            territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'land', 'value': 3},
                         4: {'type': 'sea'}},
            adjacency={1: [2, 4], 2: [1, 3, 4], 3: [2], 4: [1, 2]},
        )
        modes = {'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT}
        gs = make_state(data, owners or {1: 'NAA', 2: 'EU', 3: 'GPC'}, modes, units_by_territory=units,
                        contested=contested)
        gs.active_faction = 'GPC'
        gs.remove_units_on_surrender = True  # (the scenario rule on: settling the contests of a faction gone from them)
        engine = GameEngine(gs, data)
        engine.turn_log = TurnLog()
        return engine, gs

    def test_the_attacker_left_alone_takes_the_territory(self):
        from engine.tests.test_engine import make_unit
        engine, gs = self.board({2: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'EU')]}, {2: {'NAA', 'EU'}})
        engine.surrender('EU')
        self.assertEqual((gs.territories[2].owner, gs.territories[2].contested_by), ('NAA', None))
        captured = [e for e in engine.turn_log.events if e['kind'] == 'territory_captured']
        self.assertEqual([(e['faction'], e['territory_id'], e['previous_owner']) for e in captured], [('NAA', 2, 'EU')])

    def test_the_owner_left_alone_keeps_it(self):
        from engine.tests.test_engine import make_unit
        engine, gs = self.board({1: [make_unit('Infantry', 'NAA'), make_unit('Armor', 'GPC')]}, {1: {'NAA', 'GPC'}})
        engine.surrender('GPC')
        self.assertEqual((gs.territories[1].owner, gs.territories[1].contested_by), ('NAA', None))
        self.assertFalse([e for e in engine.turn_log.events if e['kind'] == 'territory_captured'])

    def test_a_contest_still_between_two_others_goes_on(self):
        from engine.tests.test_engine import make_unit
        engine, gs = self.board({2: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'GPC'), make_unit('Infantry', 'EU')]},
                                {2: {'NAA', 'GPC', 'EU'}})
        engine.surrender('EU')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'GPC'})
        self.assertEqual(gs.territories[2].owner, 'EU')  # (whoever wins it takes it at their Capture Territory)

    def test_a_sea_contest_with_one_side_left_ends(self):
        from engine.tests.test_engine import make_unit
        engine, gs = self.board({4: [make_unit('Cruiser', 'NAA'), make_unit('Cruiser', 'GPC')]}, {4: {'NAA', 'GPC'}})
        engine.surrender('GPC')
        self.assertIsNone(gs.territories[4].contested_by)

    def test_contests_it_had_no_part_in_are_left_alone(self):
        from engine.tests.test_engine import make_unit
        engine, gs = self.board({2: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'EU')]}, {2: {'NAA', 'EU'}})
        engine.surrender('GPC')
        self.assertEqual((gs.territories[2].owner, gs.territories[2].contested_by), ('EU', {'NAA', 'EU'}))


class TestArmistice(unittest.TestCase):
    def test_it_ends_the_game_and_logs_who_agreed(self):
        from engine.turn_log import TurnLog
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        log = TurnLog()
        engine.turn_log = log
        engine.end_by_armistice('NAA', ['NAA', 'EU', 'GPC'])
        self.assertTrue(gs.game_over)
        self.assertEqual(log.events[-1], {'kind': 'armistice', 'turn': 0, 'faction': 'NAA', 'participants': ['EU', 'GPC', 'NAA']})

    def test_nobody_is_eliminated_by_an_armistice(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT})
        engine.end_by_armistice('NAA', ['NAA', 'EU', 'GPC'])
        self.assertEqual(sorted(gs.active_factions()), ['EU', 'GPC', 'NAA'])
        self.assertFalse(any(f.eliminated for f in gs.factions.values()))

    def test_it_works_at_any_phase(self):
        for phase in (Phase.PURCHASE, Phase.COMBAT_RESOLUTION, Phase.DIPLOMACY):
            engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT}, phase=phase)
            engine.end_by_armistice('NAA', ['NAA', 'EU', 'GPC'])
            self.assertTrue(gs.game_over, phase)

    def test_cannot_end_an_already_over_game_by_armistice(self):
        engine, gs = world({'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT})
        engine.surrender('NAA')  # ends the game (last one standing)
        with self.assertRaises(ValueError):
            engine.end_by_armistice('EU', ['EU'])


if __name__ == '__main__':
    unittest.main()
