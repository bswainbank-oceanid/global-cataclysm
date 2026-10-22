"""combat.true_territory_loss, by moving out: a contested territory of yours that your Combat or Non-Combat moves
leave with none of your (or your allies') land units passes at once to whoever still occupies it -- except a
contest an alliance break made, which the former ally only claims on its own turn."""
import unittest

from engine.engine import CombatMoveOrder, GameEngine, NonCombatMoveOrder
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit
from engine.turn_log import TurnLog

# 1: NAA's, contested by AAC (both have Infantry there). 2: NAA's, next to it. 3: AAC's.
TERRITORIES = {1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 2}, 3: {'type': 'land', 'value': 2}}
ADJACENCY = {1: [2, 3], 2: [1], 3: [1]}


def board(phase, extra=None):
    data = FakeData(territories=TERRITORIES, adjacency=ADJACENCY)
    mine = make_unit('Infantry', 'NAA')
    units = {1: [mine, make_unit('Infantry', 'AAC')] + (extra or [])}
    gs = make_state(data, {1: 'NAA', 2: 'NAA', 3: 'AAC'},
                    {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.BOT, 'EU': FactionMode.BOT},
                    units_by_territory=units, contested={1: {'NAA', 'AAC'}}, phase=phase)
    gs.active_faction = 'NAA'
    engine = GameEngine(gs, data)
    engine.turn_log = TurnLog()
    return engine, gs, mine


class TestAbandoningAContestedTerritory(unittest.TestCase):
    def test_moving_out_in_non_combat_move_hands_it_over_at_once(self):
        engine, gs, mine = board(Phase.NONCOMBAT_MOVE)
        engine._execute_noncombat_moves([NonCombatMoveOrder(mine.unit_id, 2)], 'NAA', gs)
        self.assertEqual((gs.territories[1].owner, gs.territories[1].contested_by), ('AAC', None))
        captured = [e for e in engine.turn_log.events if e['kind'] == 'territory_captured']
        self.assertEqual([(e['faction'], e['territory_id'], e['previous_owner']) for e in captured], [('AAC', 1, 'NAA')])

    def test_moving_out_in_combat_move_too_recorded_once(self):
        engine, gs, mine = board(Phase.COMBAT_MOVE)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mine.unit_id, [1, 2])])  # (staged: nothing real yet)
        self.assertEqual(gs.territories[1].owner, 'NAA')
        engine.confirm_combat_moves('NAA')
        self.assertEqual((gs.territories[1].owner, gs.territories[1].contested_by), ('AAC', None))
        self.assertEqual(len([e for e in engine.turn_log.events if e['kind'] == 'territory_captured']), 1)

    def test_an_ally_still_there_keeps_it(self):
        engine, gs, mine = board(Phase.NONCOMBAT_MOVE, extra=[make_unit('Infantry', 'EU')])
        gs.factions['NAA'].alliance = gs.factions['EU'].alliance = 'pact'
        engine._execute_noncombat_moves([NonCombatMoveOrder(mine.unit_id, 2)], 'NAA', gs)
        self.assertEqual(gs.territories[1].owner, 'NAA')

    def test_a_contest_from_an_alliance_break_waits_for_the_former_allys_own_turn(self):
        engine, gs, mine = board(Phase.NONCOMBAT_MOVE)
        gs.territories[1].reclaim_bonus_for = 'NAA'  # (AAC's units were left here when it withdrew from their alliance)
        engine._execute_noncombat_moves([NonCombatMoveOrder(mine.unit_id, 2)], 'NAA', gs)
        self.assertEqual((gs.territories[1].owner, gs.territories[1].contested_by), ('NAA', {'NAA', 'AAC'}))

    def test_with_nobody_else_on_the_ground_nothing_is_handed_over(self):
        engine, gs, mine = board(Phase.NONCOMBAT_MOVE)
        gs.territories[1].units = [mine, make_unit('Fighter', 'AAC')]  # (air can't hold ground)
        engine._execute_noncombat_moves([NonCombatMoveOrder(mine.unit_id, 2)], 'NAA', gs)
        self.assertEqual(gs.territories[1].owner, 'NAA')


if __name__ == '__main__':
    unittest.main()


class TestTheEnemyWithdrawing(unittest.TestCase):
    """The other way round: the faction contesting someone else's territory pulls out in its Non-Combat Move. With
    the owner's units still there, the territory is no longer contested -- the owner keeps it, at once."""

    def enemy_turn(self, extra=None, owner_units=True):
        engine, gs, mine = board(Phase.NONCOMBAT_MOVE, extra)
        if not owner_units:
            gs.territories[1].units.remove(mine)
        gs.active_faction = 'AAC'
        [theirs] = [u for u in gs.territories[1].units if u.owner == 'AAC' and u.unit_type == 'Infantry']
        return engine, gs, theirs

    def test_it_is_no_longer_contested_and_the_owner_keeps_it(self):
        engine, gs, theirs = self.enemy_turn()
        engine.process_return_to_base('AAC')
        engine.submit_noncombat_moves('AAC', [NonCombatMoveOrder(theirs.unit_id, 3)])
        engine.confirm_noncombat_moves('AAC')
        self.assertEqual((gs.territories[1].owner, gs.territories[1].contested_by), ('NAA', None))

    def test_not_while_another_enemy_is_still_there(self):
        engine, gs, theirs = self.enemy_turn(extra=[make_unit('Infantry', 'EU')])
        gs.territories[1].contested_by = {'NAA', 'AAC', 'EU'}
        engine.process_return_to_base('AAC')
        engine.submit_noncombat_moves('AAC', [NonCombatMoveOrder(theirs.unit_id, 3)])
        engine.confirm_noncombat_moves('AAC')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'EU'})  # (AAC is out of it; EU fights on)

    def test_not_when_the_owner_has_nobody_there(self):
        engine, gs, theirs = self.enemy_turn(owner_units=False)
        engine.process_return_to_base('AAC')
        engine.submit_noncombat_moves('AAC', [NonCombatMoveOrder(theirs.unit_id, 3)])
        engine.confirm_noncombat_moves('AAC')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)  # (nobody contests it any more either)
