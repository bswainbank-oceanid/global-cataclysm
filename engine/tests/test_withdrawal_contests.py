"""Withdrawing from an alliance where the two sides' units stand together: every such LAND territory is contested,
each way round -- the former ally's where the withdrawing faction stands, and the withdrawing faction's own (its
Strategic Centers too) where a former ally stands. The owner has the first-round bonus there (attacking to clear it,
or defending it), and the other side claims it only at its own Capture Territory, as normal."""
import unittest

from engine.engine import GameEngine
from engine.state import FactionMode, Phase
from engine.tests.test_engine import FakeData, make_state, make_unit

# 1: NAA's Strategic Center, 2: NAA's land, 3: EU's land. EU stands on 1 and 2 beside NAA; NAA stands on 3 beside EU.
DATA = FakeData(territories={1: {'type': 'land', 'value': 5, 'strategic_center': True}, 2: {'type': 'land', 'value': 2},
                             3: {'type': 'land', 'value': 2}}, adjacency={1: [2], 2: [1, 3], 3: [2]})


def withdrawn():
    gs = make_state(DATA, {1: 'NAA', 2: 'NAA', 3: 'EU'},
                    {'NAA': FactionMode.HUMAN, 'EU': FactionMode.BOT, 'GPC': FactionMode.BOT},
                    units_by_territory={1: [make_unit('Infantry', 'NAA'), make_unit('Armor', 'EU')],
                                        2: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'EU')],
                                        3: [make_unit('Infantry', 'EU'), make_unit('Infantry', 'NAA')]},
                    phase=Phase.DIPLOMACY)
    gs.active_faction = 'NAA'
    gs.factions['NAA'].alliance = gs.factions['EU'].alliance = 'pact'
    engine = GameEngine(gs, DATA)
    engine.withdraw_from_alliance('NAA')
    return engine, gs


class TestWithdrawingWithUnitsSideBySide(unittest.TestCase):
    def test_the_withdrawing_factions_own_land_is_contested_its_sc_too(self):
        engine, gs = withdrawn()
        for tid in (1, 2):
            self.assertEqual(gs.territories[tid].contested_by, {'NAA', 'EU'}, tid)
            self.assertEqual(gs.territories[tid].owner, 'NAA', tid)

    def test_and_the_former_allys_as_before(self):
        engine, gs = withdrawn()
        self.assertEqual(gs.territories[3].contested_by, {'NAA', 'EU'})

    def test_each_owner_has_the_bonus_on_its_own_land(self):
        engine, gs = withdrawn()
        self.assertEqual([gs.territories[t].reclaim_bonus_for for t in (1, 2, 3)], ['NAA', 'NAA', 'EU'])
        at = lambda tid, owner: [u for u in gs.territories[tid].units if u.owner == owner]  # noqa: E731
        # NAA clearing its own SC: the attacker's bonus; EU attacking there: NAA's units defend with it
        self.assertEqual(engine.round1_bonus('NAA', 1, 'land', at(1, 'NAA'), at(1, 'EU'))[0], 'attacker')
        self.assertEqual(engine.round1_bonus('EU', 1, 'land', at(1, 'EU'), at(1, 'NAA'))[0], 'defender')
        # and the same for EU on its land
        self.assertEqual(engine.round1_bonus('EU', 3, 'land', at(3, 'EU'), at(3, 'NAA'))[0], 'attacker')

    def test_the_former_ally_claims_it_at_its_own_capture_territory(self):
        engine, gs = withdrawn()
        gs.territories[2].units = [u for u in gs.territories[2].units if u.owner == 'EU']  # (NAA's defender is gone)
        gs.active_faction, gs.phase = 'EU', Phase.CAPTURE
        engine.process_capture_territory('EU')
        self.assertEqual(gs.territories[2].owner, 'EU')
        self.assertFalse(gs.territories[2].contested_by)


if __name__ == '__main__':
    unittest.main()
