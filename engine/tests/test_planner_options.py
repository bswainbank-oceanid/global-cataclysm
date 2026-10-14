"""The planner options a bot set may turn on (engine/bots/strategy_settings.py PLANNER_OPTIONS) -- on in the
alternate strategy profiles, off in the standard ones:
- carrier_for_sea_aircraft: an aircraft bought into a sea zone comes with a place on a carrier there
- no_mech_inf_for_control_oceans: Control Oceans buys no Mechanized Infantry
- amphibious_sc_pursuit: an SC pursuit across the sea buys Mechanized Infantry first, escorted at sea"""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from server.lobby import build_session
from server.tests.test_persist import bot, seat

MECH, CARRIER, CRUISER = 'Mechanized Infantry', 'Aircraft Carrier', 'Cruiser'


def planner(bot_set, objective, seed=41):
    session, _ = build_session({'seats': [bot('strategy'), bot('strategy'), seat('NONCOMBATANT'), seat('NONCOMBATANT'),
                                          seat('NONCOMBATANT'), seat('NONCOMBATANT')], 'seed': seed})
    engine = session.engine
    me = engine.game_state.active_faction
    engine.game_state.factions[me].treasury_mpc = 1000  # (plenty to buy with)
    p = Planner(engine, me, load_settings(engine.data, bot_set), 'Strategic', random.Random(1), 'full', 50)
    p._objective = objective
    return p


def quiet_sea_spot(p, room=2):
    """A sea zone I may buy into that has nothing of anyone's in it, with room to deploy `room` units this turn."""
    from engine.engine import PurchaseOrder
    sc_spots, other = p.engine.legal_purchase_targets(p.me)
    for tid in sorted(set(sc_spots) | set(other)):
        if not p.is_land(tid) and not p.gs.territories[tid].units and p.sea_zone_safe(tid):
            try:
                p.engine._resolve_and_cost([PurchaseOrder('Fighter', room, tid)], p.me)
            except ValueError:
                continue
            return tid
    raise AssertionError(f'no quiet sea zone with room for {room} to buy into')


def bought(p, tid):
    return sorted(t for (t, where), q in p.purchases.items() if where == tid for _ in range(q))


class TestCarrierForSeaAircraft(unittest.TestCase):
    def test_an_aircraft_at_sea_comes_with_a_carrier_in_the_alternate_set_only(self):
        for bot_set, expected in (('alternate', [CARRIER, 'Fighter']), ('standard', ['Fighter'])):
            p = planner(bot_set, 'control_oceans')
            tid = quiet_sea_spot(p)
            self.assertIsNotNone(p.buy(tid, ('Air',), 'control_oceans', only=('Fighter',)), bot_set)
            self.assertEqual(bought(p, tid), expected, bot_set)

    def test_a_carrier_with_room_needs_no_second(self):
        p = planner('alternate', 'pursue_sc_1')
        tid = quiet_sea_spot(p, room=6)
        for _ in range(3):  # (a carrier holds three)
            p.buy(tid, ('Air',), 'pursue_sc_1', only=('Fighter',))
        self.assertEqual(bought(p, tid), [CARRIER] + ['Fighter'] * 3)
        p.buy(tid, ('Air',), 'pursue_sc_1', only=('Fighter',))
        self.assertEqual(bought(p, tid).count(CARRIER), 2)  # (the fourth needs another)


class TestCarriersOnlyWithAircraft(unittest.TestCase):
    def test_a_carrier_is_never_bought_on_its_own(self):
        p = planner('alternate', 'control_oceans')
        tid = quiet_sea_spot(p, room=6)
        self.assertIsNone(p.buy(tid, ('Sea',), 'control_oceans', only=(CARRIER,)))
        for _ in range(5):
            p.buy(tid, ('Sea',), 'control_oceans')
        self.assertNotIn(CARRIER, bought(p, tid))
        p = planner('standard', 'control_oceans')  # (the standard set: as before)
        tid = quiet_sea_spot(p)
        self.assertIsNotNone(p.buy(tid, ('Sea',), 'control_oceans', only=(CARRIER,)))

    def test_when_the_pair_cant_be_bought_a_submarine_or_cruiser_instead(self):
        p = planner('alternate', 'pursue_sc_1')
        tid = quiet_sea_spot(p)
        p.gs.factions[p.me].treasury_mpc = 15  # (a Fighter and a carrier: 21)
        stub = p.buy(tid, ('Air',), 'pursue_sc_1', only=('Fighter', 'Bomber'))
        self.assertIn(stub.unit_type, ('Submarine', CRUISER))
        self.assertEqual(bought(p, tid), [stub.unit_type])  # (no aircraft, no carrier)


class TestHoldOneBattle(unittest.TestCase):
    def test_defenders_left_only_in_the_air_dont_hold_the_ground(self):
        from engine.bots import battle_sim
        from engine.tests.test_engine import make_unit
        session, _ = build_session({'seats': [bot('strategy'), bot('strategy')] + [seat('NONCOMBATANT')] * 4, 'seed': 41})
        data = session.engine.data
        attackers = [make_unit('Infantry', 'X'), make_unit('Infantry', 'X')]
        defenders = [make_unit('Fighter', 'Y')]  # (a defender with no land unit at all)
        whole = battle_sim.estimate(attackers, defenders, 'land', data.units(), data.rules(), random.Random(1), 400,
                                    max_rounds=3, capture=True)
        held = battle_sim.estimate(attackers, defenders, 'land', data.units(), data.rules(), random.Random(1), 400,
                                   max_rounds=3, capture=True, land_hold=True)
        self.assertGreater(held.attacker_wins, whole.attacker_wins)  # (the Fighter still flying doesn't save it)

    def test_one_battle_needs_no_more_defenders_than_a_fight_to_the_finish(self):
        from engine.tests.test_keep_last_defender import border_board
        _, planner, fronts = border_board(61)
        alt, std = planner('alternate'), planner('standard')
        for tid in fronts:
            defenders = alt.defenders_at(tid, claimed_only=False)
            self.assertGreaterEqual(alt.hold_chance(tid, defenders, fast=False, samples=400),
                                    std.hold_chance(tid, defenders, fast=False, samples=400) - 0.05, tid)


class TestExpandNearestFirst(unittest.TestCase):
    def test_expand_weighs_only_what_free_land_units_can_reach_weakest_nearest_first(self):
        from engine.tests.test_keep_last_defender import border_board
        _, planner, _ = border_board(62)
        p = planner('alternate')
        p.run()
        reach = planner('alternate')._free_reach()
        considered = [a['target'] for a in p.attempts if a['objective'] == 'expand_territory']
        self.assertTrue(considered)
        self.assertTrue(all(t in reach for t in considered), considered)


class TestNoMechInfForControlOceans(unittest.TestCase):
    def test_control_oceans_never_buys_mech_inf_in_the_alternate_set(self):
        p = planner('alternate', 'control_oceans')
        tid = quiet_sea_spot(p)
        self.assertIsNone(p.buy(tid, ('Land',), 'control_oceans'))
        p = planner('standard', 'control_oceans')
        self.assertIsNotNone(p.buy(quiet_sea_spot(p), ('Land',), 'control_oceans'))  # (it could, if ever asked)


class TestAmphibiousScPursuit(unittest.TestCase):
    def test_mech_inf_at_sea_for_a_pursuit_comes_with_an_escort(self):
        for bot_set, expected in (('alternate', [CRUISER, MECH]), ('standard', [MECH])):
            p = planner(bot_set, 'pursue_sc_1')
            tid = quiet_sea_spot(p)
            p.buy(tid, ('Land',), 'pursue_sc_1', only=(MECH,))
            self.assertEqual(bought(p, tid), expected, bot_set)
        p = planner('alternate', 'pursue_sc_1')
        tid = quiet_sea_spot(p, room=3)
        p.buy(tid, ('Land',), 'pursue_sc_1', only=(MECH,))
        p.buy(tid, ('Land',), 'pursue_sc_1', only=(MECH,))
        self.assertEqual(bought(p, tid), [CRUISER, MECH, MECH])  # (one escort is enough)

    def test_an_overseas_target_gets_amphibious_land_units_first(self):
        p = planner('alternate', 'pursue_sc_1')
        mine = [t for t in p.terrs if p.is_land(t) and p.owner(t) == p.me]
        spot = mine[0]
        overseas = next(t for t in p.terrs if p.is_land(t) and t not in p._landmass(spot))
        p._target = overseas
        self.assertEqual(p._island_land_types(spot), (MECH,))
        p.settings = load_settings(p.data, 'standard')
        if overseas not in __import__('engine.deployment', fromlist=['islands']).islands(p.data):
            self.assertIsNone(p._island_land_types(spot))  # (the standard set: only islands limit them)


if __name__ == '__main__':
    unittest.main()
