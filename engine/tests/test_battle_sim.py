"""The strategy bots' fast battle estimator must agree with the real resolver."""
import copy
import random
import time
import unittest

from engine import data
from engine.bots import battle_sim
from engine.combat import EventKind, resolve_battle
from engine.state import UnitInstance

UNIT_DEFS = data.units()
RULES = data.rules()
UNLIMITED = copy.deepcopy(RULES)
UNLIMITED['combat']['rounds_per_battle'] = 200


def mk(uid, unit_type, owner, promotions=0):
    return UnitInstance(unit_id=uid, unit_type=unit_type, owner=owner,
                        current_hp=UNIT_DEFS[unit_type]['hp'] + promotions, promotions=promotions)


def engine_odds(attackers, defenders, battle_type, samples=1500, bonus=None, seed=1):
    """The real resolver, run without a round limit."""
    rng = random.Random(seed)
    counts = {'attacker_wins': 0, 'defender_wins': 0}
    for _ in range(samples):
        a = [copy.copy(u) for u in attackers]
        d = [copy.copy(u) for u in defenders]
        outcome = None
        for e in resolve_battle(a, d, battle_type, rng, 0, UNIT_DEFS, UNLIMITED, round1_bonus_side=bonus):
            if e.kind == EventKind.BATTLE_END:
                outcome = e.outcome
        if outcome == 'defender_eliminated':
            counts['attacker_wins'] += 1
        elif outcome == 'attacker_eliminated':
            counts['defender_wins'] += 1
    return counts['attacker_wins'] / samples, counts['defender_wins'] / samples


def fast_odds(attackers, defenders, battle_type, samples=6000, bonus=None, seed=2):
    o = battle_sim.estimate(attackers, defenders, battle_type, UNIT_DEFS, RULES, random.Random(seed), samples, bonus)
    return o.attacker_wins, o.defender_wins


SCENARIOS = {
    'even land': (lambda: [mk(1, 'Armor', 'A'), mk(2, 'Infantry', 'A'), mk(3, 'Mechanized Infantry', 'A')],
                  lambda: [mk(11, 'Armor', 'B'), mk(12, 'Infantry', 'B'), mk(13, 'Infantry', 'B')], 'land', None),
    'big attack': (lambda: [mk(i, t, 'A') for i, t in enumerate(['Armor', 'Armor', 'Mechanized Infantry', 'Infantry', 'Fighter', 'Bomber'], 1)],
                   lambda: [mk(11, 'Infantry', 'B'), mk(12, 'Infantry', 'B'), mk(13, 'Armor', 'B')], 'land', None),
    'air superiority': (lambda: [mk(1, 'Fighter', 'A'), mk(2, 'Bomber', 'A'), mk(3, 'Armor', 'A')],
                        lambda: [mk(11, 'Fighter', 'B'), mk(12, 'Infantry', 'B'), mk(13, 'Infantry', 'B')], 'land', None),
    'promoted defender': (lambda: [mk(1, 'Armor', 'A'), mk(2, 'Armor', 'A')],
                          lambda: [mk(11, 'Armor', 'B', promotions=2), mk(12, 'Infantry', 'B', promotions=1)], 'land', None),
    'sea fleet': (lambda: [mk(1, 'Cruiser', 'A'), mk(2, 'Submarine', 'A'), mk(3, 'Aircraft Carrier', 'A'), mk(4, 'Fighter', 'A')],
                  lambda: [mk(11, 'Cruiser', 'B'), mk(12, 'Submarine', 'B'), mk(13, 'Submarine', 'B')], 'sea', None),
    'transports': (lambda: [mk(1, 'Cruiser', 'A'), mk(2, 'Cruiser', 'A')],
                   lambda: [mk(11, 'Infantry', 'B'), mk(12, 'Mechanized Infantry', 'B'), mk(13, 'Submarine', 'B')], 'sea', None),
    'amphibious bonus': (lambda: [mk(1, 'Armor', 'A'), mk(2, 'Mechanized Infantry', 'A'), mk(3, 'Infantry', 'A')],
                         lambda: [mk(11, 'Armor', 'B'), mk(12, 'Infantry', 'B')], 'land', 'defender'),
    'one rank from the cap': (lambda: [mk(1, 'Armor', 'A', promotions=4), mk(2, 'Infantry', 'A', promotions=4), mk(3, 'Armor', 'A', promotions=5)],
                              lambda: [mk(11, 'Armor', 'B', promotions=4), mk(12, 'Infantry', 'B', promotions=5), mk(13, 'Infantry', 'B')], 'land', None),
    'submarines v aircraft': (lambda: [mk(1, 'Submarine', 'A'), mk(2, 'Submarine', 'A')],
                              lambda: [mk(11, 'Bomber', 'B'), mk(12, 'Cruiser', 'B')], 'sea', None),
}


class TestFastEstimatorAgreesWithTheResolver(unittest.TestCase):
    def test_scenarios(self):
        for name, (att, dfn, kind, bonus) in SCENARIOS.items():
            with self.subTest(name):
                real = engine_odds(att(), dfn(), kind, bonus=bonus)
                fast = fast_odds(att(), dfn(), kind, bonus=bonus)
                self.assertAlmostEqual(real[0], fast[0], delta=0.05, msg=f'attacker wins: real {real[0]:.3f} vs fast {fast[0]:.3f}')
                self.assertAlmostEqual(real[1], fast[1], delta=0.05, msg=f'defender wins: real {real[1]:.3f} vs fast {fast[1]:.3f}')

    def test_no_one_to_fight(self):
        a = [mk(1, 'Armor', 'A')]
        self.assertEqual(battle_sim.estimate(a, [], 'land', UNIT_DEFS, RULES).attacker_wins, 1.0)
        self.assertEqual(battle_sim.estimate([], a, 'land', UNIT_DEFS, RULES).defender_wins, 1.0)

    def test_units_are_not_modified(self):
        a = [mk(1, 'Armor', 'A')]
        d = [mk(2, 'Infantry', 'B')]
        before = (a[0].current_hp, d[0].current_hp, a[0].xp)
        battle_sim.estimate(a, d, 'land', UNIT_DEFS, RULES, random.Random(1), 50)
        self.assertEqual((a[0].current_hp, d[0].current_hp, a[0].xp), before)

    def test_it_is_fast_enough_to_plan_with(self):
        a, d, kind, bonus = SCENARIOS['big attack'][0](), SCENARIOS['big attack'][1](), 'land', None
        start = time.time()
        battle_sim.estimate(a, d, kind, UNIT_DEFS, RULES, random.Random(3), 200)
        self.assertLess(time.time() - start, 0.15, '200 samples should take a few milliseconds each at most')

    def test_the_obvious_cases(self):
        strong = [mk(i, 'Armor', 'A') for i in range(1, 7)]
        weak = [mk(20, 'Infantry', 'B')]
        self.assertGreater(battle_sim.estimate(strong, weak, 'land', UNIT_DEFS, RULES, random.Random(1)).attacker_wins, 0.97)
        self.assertLess(battle_sim.estimate(weak, strong, 'land', UNIT_DEFS, RULES, random.Random(1)).attacker_wins, 0.03)


if __name__ == '__main__':
    unittest.main()
