import random
import unittest

from engine import data
from engine.state import UnitInstance
from engine.combat import resolve_battle, BattleResult, EventKind, _select_target, _resolution_sequence

UNIT_DEFS = data.units()
RULES = data.rules()


class ScriptedRNG:
    """Test double: randint() returns values from a scripted list in
    order (one entry consumed per die roll); choices() always picks the
    first candidate in the pool, ignoring weights -- fine for tests that
    only care about hit/damage math or resolution order, not which
    specific unit got targeted among ties."""
    def __init__(self, rolls):
        self.rolls = list(rolls)

    def randint(self, a, b):
        return self.rolls.pop(0)

    def choices(self, population, weights=None, k=1):
        return [population[0]]


def make(uid, unit_type, owner, hp=None, promoted=False):
    hp = UNIT_DEFS[unit_type]['hp'] + (1 if promoted else 0) if hp is None else hp
    return UnitInstance(unit_id=uid, unit_type=unit_type, owner=owner, current_hp=hp, promoted=promoted)


def drain(attackers, defenders, battle_type, rng, turn=0):
    return list(resolve_battle(attackers, defenders, battle_type, rng, turn, UNIT_DEFS, RULES))


class TestHitAndDamageMath(unittest.TestCase):
    def test_normal_hit_deals_full_damage(self):
        # Armor: D8, damage 4. A roll of 5 hits Infantry's defense (5)
        # without being Armor's die-max (8), so damage isn't halved.
        # Infantry (hp 2) dies to the hit, ending the battle after round
        # 1 -- attacker's roll, then defender's (a guaranteed miss: D6
        # can't reach Armor's defense of 7), 2 scripted rolls is enough.
        attacker = make(1, 'Armor', 'NAA')  # D8, damage 4, defense 7
        defender = make(2, 'Infantry', 'AAC')  # defense 5, hp 2
        events = drain([attacker], [defender], 'land', ScriptedRNG([5, 1]))
        roll_events = [e for e in events if e.kind == EventKind.UNIT_ROLL]
        e = roll_events[0]
        self.assertEqual(e.side, 'attacker')
        self.assertTrue(e.hit)
        self.assertEqual(e.damage, 4)  # full damage, not halved
        self.assertEqual(e.target_hp_after, -2)

    def test_max_die_roll_always_hits_regardless_of_defense_and_halves_damage(self):
        # Infantry: D6 (max 6), damage 2. Armor's defense (7) is HIGHER
        # than Infantry's max possible roll, so under the plain roll >=
        # defense rule, a 6 would normally miss (6 < 7) -- this is the
        # case that actually isolates the bypass, unlike a big-die
        # attacker whose max roll would already beat any real unit's
        # defense on its own. Because 6 is Infantry's die max, it still
        # forces a hit, with damage halved (2 // 2 = 1), enough to kill
        # this hp-1 Armor.
        attacker = make(1, 'Infantry', 'NAA')  # D6, damage 2, defense 5
        defender = make(2, 'Armor', 'AAC', hp=1)  # defense 7 -- unreachable by a plain D6 roll
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]))
        e = [ev for ev in events if ev.kind == EventKind.UNIT_ROLL][0]
        self.assertTrue(e.hit)
        self.assertEqual(e.damage, 1)  # damage 2 // 2

    def test_below_defense_roll_misses_but_still_shows_a_target(self):
        attacker = make(1, 'Infantry', 'NAA')  # D6, can't reach a defense of 7
        defender = make(2, 'Armor', 'AAC')  # defense 7 -- unreachable by a D6
        # Neither side can hit the other (attacker's D6 tops out at 6,
        # defender's D8 rolls scripted low) -- goes all 3 rounds, 6 rolls.
        events = drain([attacker], [defender], 'land', ScriptedRNG([3, 1, 3, 1, 3, 1]))
        e = [ev for ev in events if ev.kind == EventKind.UNIT_ROLL][0]
        self.assertFalse(e.hit)
        self.assertEqual(e.damage, 0)
        self.assertEqual(e.target_unit_id, 2)  # still nominally targeted, for display


class TestResolutionOrder(unittest.TestCase):
    def test_grouped_by_type_then_die_size_ascending(self):
        units = [
            make(1, 'Bomber', 'NAA'),
            make(2, 'Infantry', 'NAA'),
            make(3, 'Armor', 'NAA', promoted=True),  # D8 -> D10 when promoted
            make(4, 'Fighter', 'NAA'),
        ]
        order = ['Infantry', 'Mechanized Infantry', 'Armor', 'Fighter', 'Bomber']
        seq = _resolution_sequence(units, UNIT_DEFS, order)
        self.assertEqual([u.unit_id for u in seq], [2, 3, 4, 1])

    def test_promoted_and_unpromoted_same_type_ordered_by_die_size(self):
        low = make(1, 'Infantry', 'NAA')  # D6
        high = make(2, 'Infantry', 'NAA', promoted=True)  # D8
        seq = _resolution_sequence([high, low], UNIT_DEFS, ['Infantry'])
        self.assertEqual([u.unit_id for u in seq], [1, 2])


class TestTargetSelectionWeighting(unittest.TestCase):
    def test_same_type_weighted_2to1_except_for_bomber_attacker(self):
        rng = random.Random(123)
        infantry = make(1, 'Infantry', 'AAC')
        armor = make(2, 'Armor', 'AAC')
        # Attacker is Infantry: should weight the Infantry defender 2x.
        _, hit = _select_target(rng, 6, 6, 'Infantry', [infantry, armor], UNIT_DEFS, 2, 1, {})
        counts = {1: 0, 2: 0}
        for _ in range(2000):
            t, _ = _select_target(rng, 6, 6, 'Infantry', [infantry, armor], UNIT_DEFS, 2, 1, {})
            counts[t.unit_id] += 1
        ratio = counts[1] / counts[2]
        self.assertTrue(1.6 < ratio < 2.4, f'expected ~2:1 same-type weighting, got {counts}')

        # Attacker is a Bomber: uniform weighting even against a same-type target.
        bomber_defender = make(3, 'Bomber', 'AAC')
        other = make(4, 'Fighter', 'AAC')
        counts2 = {3: 0, 4: 0}
        for _ in range(2000):
            t, _ = _select_target(rng, 12, 12, 'Bomber', [bomber_defender, other], UNIT_DEFS, 2, 1, {})
            counts2[t.unit_id] += 1
        ratio2 = counts2[3] / counts2[4]
        self.assertTrue(0.8 < ratio2 < 1.2, f'expected ~1:1 (bomber attacker), got {counts2}')

    def test_progressive_elimination_excludes_already_knocked_out_targets(self):
        weak = make(1, 'Infantry', 'AAC', hp=1)
        tough = make(2, 'Armor', 'AAC', hp=4)
        # weak already has 1 pending damage -- exactly its HP -- so it must
        # be excluded from targeting even though current_hp still reads 1.
        pending = {1: 1}
        target, _ = _select_target(random.Random(1), 6, 6, 'Infantry', [weak, tough], UNIT_DEFS, 2, 1, pending)
        self.assertEqual(target.unit_id, 2)


class TestBattleOutcomes(unittest.TestCase):
    def test_three_round_cap_produces_contested_when_both_sides_survive(self):
        # Two lone Infantry that keep missing each other (defense 5,
        # scripted rolls below it) never resolve -- must stop at round 3.
        attacker = make(1, 'Infantry', 'NAA')
        defender = make(2, 'Infantry', 'AAC')
        rolls = [1] * 6  # 3 rounds x (1 attacker roll + 1 defender roll), no air units involved
        events = drain([attacker], [defender], 'land', ScriptedRNG(rolls))
        end = events[-1]
        self.assertEqual(end.kind, EventKind.BATTLE_END)
        self.assertEqual(end.outcome, 'contested')
        self.assertEqual(max(e.round_number for e in events if e.kind == EventKind.ROUND_START), 3)

    def test_defender_eliminated_when_attacker_survives(self):
        attacker = make(1, 'Armor', 'NAA', hp=4)  # D8, damage 4
        defender = make(2, 'Infantry', 'AAC', hp=2)  # defense 5, hp 2
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]))  # attacker hits, defender misses
        result = BattleResult.from_events(events)
        self.assertEqual(result.outcome, 'defender_eliminated')
        self.assertEqual(result.surviving_attacker_ids, [1])
        self.assertEqual(result.eliminated_defender_ids, [2])


class TestPromotion(unittest.TestCase):
    def test_unit_promotes_after_crossing_xp_threshold_and_heals_bonus_hp_immediately(self):
        attacker = make(1, 'Armor', 'NAA', hp=4)  # D8, damage 4, defense 7
        # High HP so it survives all 3 rounds of the attacker's hits;
        # its own D6 roll (defense needed: 7) always misses back.
        defender = make(2, 'Infantry', 'AAC', hp=20)
        # Attacker: roll 6 each round -- hits Infantry's defense (5)
        # without being Armor's die-max (8), so it lands a hit (survive
        # +1, deal damage +1) every round: 3 rounds x 2xp = 6xp, crossing
        # the 5xp threshold. Defender: roll 1 each round, always misses.
        rolls = [6, 1, 6, 1, 6, 1]
        drain([attacker], [defender], 'land', ScriptedRNG(rolls))
        self.assertTrue(attacker.promoted)
        self.assertEqual(attacker.current_hp, 5)  # base 4 + promotion's +1 HP


class TestAutoPlayVsInteractiveParity(unittest.TestCase):
    def test_stepping_with_next_matches_draining_the_generator(self):
        def squad():
            return (
                [make(1, 'Infantry', 'NAA'), make(2, 'Armor', 'NAA')],
                [make(3, 'Infantry', 'AAC'), make(4, 'Armor', 'AAC')],
            )
        a1, d1 = squad()
        events_auto = drain(a1, d1, 'land', random.Random(99), turn=2)

        a2, d2 = squad()
        gen = resolve_battle(a2, d2, 'land', random.Random(99), 2, UNIT_DEFS, RULES)
        events_manual = []
        for event in gen:
            events_manual.append(event)

        self.assertEqual(events_auto, events_manual)
        self.assertEqual([u.current_hp for u in a1], [u.current_hp for u in a2])


class TestAirSuperiorityTrigger(unittest.TestCase):
    def test_no_trigger_when_both_sides_are_bomber_only(self):
        events = drain([make(1, 'Bomber', 'NAA')], [make(2, 'Bomber', 'AAC')], 'land', random.Random(1))
        self.assertFalse(any(e.kind == EventKind.AIR_SUPERIORITY_START for e in events))

    def test_triggers_when_either_side_has_a_fighter(self):
        events = drain([make(1, 'Fighter', 'NAA')], [make(2, 'Bomber', 'AAC')], 'land', random.Random(1))
        self.assertTrue(any(e.kind == EventKind.AIR_SUPERIORITY_START for e in events))

    def test_no_trigger_when_one_side_has_no_air_units(self):
        events = drain([make(1, 'Infantry', 'NAA')], [make(2, 'Fighter', 'AAC')], 'land', random.Random(1))
        self.assertFalse(any(e.kind == EventKind.AIR_SUPERIORITY_START for e in events))


class TestMutualElimination(unittest.TestCase):
    def test_both_sides_wiped_in_the_same_round_reports_mutual_elimination(self):
        attacker = make(1, 'Infantry', 'NAA', hp=1)
        defender = make(2, 'Infantry', 'AAC', hp=1)
        # both roll high enough to hit each other's defense (5) in round 1
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 6]))
        end = events[-1]
        self.assertEqual(end.outcome, 'mutual_elimination')
        self.assertEqual(end.surviving_attacker_ids, [])
        self.assertEqual(end.surviving_defender_ids, [])


if __name__ == '__main__':
    unittest.main()
