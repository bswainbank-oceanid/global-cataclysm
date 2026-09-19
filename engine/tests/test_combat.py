import random
import unittest

from engine import data
from engine.state import UnitInstance
from engine.combat import resolve_battle, BattleResult, EventKind, _select_target, _resolution_sequence, _fight_one_round

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


def drain(attackers, defenders, battle_type, rng, turn=0, round1_bonus_side=None):
    return list(resolve_battle(attackers, defenders, battle_type, rng, turn, UNIT_DEFS, RULES, round1_bonus_side=round1_bonus_side))


def combat_cfg():
    cfg = dict(RULES['combat'])
    cfg['_promotion_cfg'] = RULES['promotion']
    return cfg


class TestHitAndDamageMath(unittest.TestCase):
    def test_normal_hit_deals_full_damage(self):
        # Armor: D8, damage 4. Infantry defends with Dig In (+1 defense,
        # always, per combat.first_round_bonuses' sibling rule), so its
        # effective defense here is 6, not the base 5 -- a roll of 6
        # hits it cleanly without being Armor's die-max (8), so damage
        # isn't halved. Infantry (hp 2) dies to the hit, ending the
        # battle after round 1 -- attacker's roll, then defender's (a
        # guaranteed miss: D6 can't reach Armor's defense of 7), 2
        # scripted rolls is enough.
        attacker = make(1, 'Armor', 'NAA')  # D8, damage 4, defense 7
        defender = make(2, 'Infantry', 'AAC')  # defense 5 (+1 Dig In while defending = 6), hp 2
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]))
        roll_events = [e for e in events if e.kind == EventKind.UNIT_ROLL]
        e = roll_events[0]
        self.assertEqual(e.side, 'attacker')
        self.assertTrue(e.hit)
        self.assertFalse(e.bypass_hit)
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
        self.assertTrue(e.bypass_hit)
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
        infantry = make(1, 'Infantry', 'AAC')  # defense 5
        armor = make(2, 'Armor', 'AAC')  # defense 7
        # roll=7 (below a die max of 8, so this is an ordinary clean-hit
        # pool, not the bypass path) reaches both defenders' defense --
        # needed so both are actually in the eligible pool to weight
        # between; a max roll would exclude the non-clean one entirely
        # once a clean hit is available (see _select_target).
        counts = {1: 0, 2: 0}
        for _ in range(2000):
            t, _, _ = _select_target(rng, 7, 8, 'Infantry', [infantry, armor], UNIT_DEFS, 2, 1, {})
            counts[t.unit_id] += 1
        ratio = counts[1] / counts[2]
        self.assertTrue(1.6 < ratio < 2.4, f'expected ~2:1 same-type weighting, got {counts}')

        # Attacker is a Bomber: uniform weighting even against a same-type target.
        bomber_defender = make(3, 'Bomber', 'AAC')  # defense 7
        other = make(4, 'Fighter', 'AAC')  # defense 8
        counts2 = {3: 0, 4: 0}
        for _ in range(2000):
            t, _, _ = _select_target(rng, 8, 10, 'Bomber', [bomber_defender, other], UNIT_DEFS, 2, 1, {})
            counts2[t.unit_id] += 1
        ratio2 = counts2[3] / counts2[4]
        self.assertTrue(0.8 < ratio2 < 1.2, f'expected ~1:1 (bomber attacker), got {counts2}')

    def test_progressive_elimination_excludes_already_knocked_out_targets(self):
        weak = make(1, 'Infantry', 'AAC', hp=1)
        tough = make(2, 'Armor', 'AAC', hp=4)
        # weak already has 1 pending damage -- exactly its HP -- so it must
        # be excluded from targeting even though current_hp still reads 1.
        pending = {1: 1}
        target, _, _ = _select_target(random.Random(1), 6, 6, 'Infantry', [weak, tough], UNIT_DEFS, 2, 1, pending)
        self.assertEqual(target.unit_id, 2)

    def test_clean_hit_preferred_over_bypass_when_both_available(self):
        # roll = die_max (6), so the bypass *could* reach Armor (defense
        # 7 > 6), but Infantry (defense 5 <= 6) is a clean hit -- must
        # take the clean hit, at full damage, not the bypass.
        clean = make(1, 'Infantry', 'AAC')  # defense 5
        needs_bypass = make(2, 'Armor', 'AAC')  # defense 7
        for _ in range(200):
            target, is_hit, is_bypass = _select_target(
                random.Random(), 6, 6, 'Mechanized Infantry', [clean, needs_bypass], UNIT_DEFS, 2, 1, {})
            self.assertTrue(is_hit)
            self.assertFalse(is_bypass)
            self.assertEqual(target.unit_id, 1)

    def test_bypass_only_used_once_all_clean_targets_are_gone(self):
        needs_bypass = make(1, 'Armor', 'AAC')  # defense 7, only reachable via bypass at roll 6
        target, is_hit, is_bypass = _select_target(
            random.Random(1), 6, 6, 'Infantry', [needs_bypass], UNIT_DEFS, 2, 1, {})
        self.assertTrue(is_hit)
        self.assertTrue(is_bypass)
        self.assertEqual(target.unit_id, 1)

    def test_bypass_pool_restricted_to_next_highest_defense_tier(self):
        # Mech Inf attacker (D6, max roll 6): no clean target exists (all
        # defenses > 6). Armor (defense 7) and a *promoted* Mech Inf
        # (base defense 6 + 1 = 7 -- a plain one would be defense 6 and
        # wrongly land in the clean pool at roll 6) both sit at defense
        # 7, the tier immediately above the roll, and are the only
        # bypass candidates, weighted 2:1 toward the same-type (Mech
        # Inf) one. A defense-8 Fighter is a tougher tier still and must
        # be excluded from the bypass pool entirely, not just deprioritized.
        armor = make(1, 'Armor', 'AAC')  # defense 7
        mech_inf = make(2, 'Mechanized Infantry', 'AAC', promoted=True)  # defense 7
        fighter = make(3, 'Fighter', 'AAC')  # defense 8
        counts = {1: 0, 2: 0, 3: 0}
        rng = random.Random(5)
        for _ in range(2000):
            target, is_hit, is_bypass = _select_target(
                rng, 6, 6, 'Mechanized Infantry', [armor, mech_inf, fighter], UNIT_DEFS, 2, 1, {})
            self.assertTrue(is_hit)
            self.assertTrue(is_bypass)
            counts[target.unit_id] += 1
        self.assertEqual(counts[3], 0, 'defense-8 Fighter should never be in the bypass pool')
        ratio = counts[2] / counts[1]
        self.assertTrue(1.6 < ratio < 2.4, f'expected ~2:1 toward same-type Mech Inf, got {counts}')


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


class TestFirstRoundCombatBonus(unittest.TestCase):
    """combat.first_round_bonuses' mechanism: resolve_battle's
    round1_bonus_side steps the recipient side's attack die up one size
    and adds +1 defense (capped), same transform as a promotion, for
    round 1 of main combat only -- never the air-superiority round,
    never rounds 2-3 -- and stacks additively with an existing
    promotion. WHICH case (if any) applies to a real battle -- amphibious
    landing, sea-deploy surprise, former-ally reclaim -- is engine.py's
    job (not yet built) to decide; these tests drive the mechanism
    directly via round1_bonus_side, the same way TestTargetSelectionWeighting
    drives _select_target directly."""

    def _run_round(self, round_number, attacker, defender, rolls, round1_bonus_side):
        rng = ScriptedRNG(rolls)
        return list(_fight_one_round(
            rng, round_number, [attacker], [defender], UNIT_DEFS, combat_cfg(),
            RULES['combat']['resolution_order']['land'], 0, round1_bonus_side=round1_bonus_side,
        ))

    def test_defender_bonus_raises_defense_and_can_turn_a_hit_into_a_miss(self):
        # Armor (no Dig In, unlike Infantry -- kept out of this pair
        # specifically to isolate the round-1 bonus from that unrelated,
        # always-on rule) has defense 7 normally; a roll of 7 would hit.
        # With the round-1 defender bonus (+1 defense, capped at 10) it
        # becomes 8, and 7 is neither >= 8 nor the attacker's die-max
        # (8), so this is a genuine miss, not even a bypass.
        attacker = make(1, 'Armor', 'NAA')
        defender = make(2, 'Armor', 'AAC')
        events = self._run_round(1, attacker, defender, rolls=[7, 1], round1_bonus_side='defender')
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        self.assertFalse(attacker_roll.hit, 'the round-1 defender bonus should have turned this into a miss')

    def test_bonus_does_not_apply_outside_round_1(self):
        attacker = make(1, 'Armor', 'NAA')
        defender = make(2, 'Armor', 'AAC')
        events = self._run_round(2, attacker, defender, rolls=[7, 1], round1_bonus_side='defender')
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        self.assertTrue(attacker_roll.hit, 'round 2 should see the defender back at its normal, unboosted defense')

    def test_attacker_bonus_steps_up_the_attack_die(self):
        attacker = make(1, 'Infantry', 'NAA')  # base D6
        defender = make(2, 'Infantry', 'AAC')
        events = self._run_round(1, attacker, defender, rolls=[3, 1], round1_bonus_side='attacker')
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        self.assertEqual(attacker_roll.die, 'D8', 'round-1 attacker bonus should step Infantry up from D6 to D8')

    def test_bonus_stacks_with_an_existing_promotion(self):
        # Promotion alone: D6 -> D8. The round-1 bonus on top: D8 -> D10.
        attacker = make(1, 'Infantry', 'NAA', promoted=True)
        defender = make(2, 'Infantry', 'AAC')
        events = self._run_round(1, attacker, defender, rolls=[3, 1], round1_bonus_side='attacker')
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        self.assertEqual(attacker_roll.die, 'D10', 'promotion (D6->D8) and the round-1 bonus (D8->D10) should stack')

    def test_air_superiority_round_is_never_boosted_even_if_requested(self):
        attacker = make(1, 'Infantry', 'NAA')
        defender = make(2, 'Infantry', 'AAC')
        events = self._run_round(0, attacker, defender, rolls=[3, 1], round1_bonus_side='attacker')
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        self.assertEqual(attacker_roll.die, 'D6', 'round_number 0 (air superiority) must never receive the round-1-only bonus')

    def test_resolve_battle_plumbs_round1_bonus_side_through_to_round_1(self):
        # End-to-end smoke test of the public API, not just the isolated
        # _fight_one_round helper the other tests above use.
        attacker = make(1, 'Infantry', 'NAA', hp=2)
        defender = make(2, 'Infantry', 'AAC', hp=2)
        # Defender gets Dig In while defending (+1 defense, always) --
        # effective defense 6, not the base 5 -- so the roll needs to
        # clear that to register as a clean hit here.
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]), round1_bonus_side='attacker')
        round1_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker' and e.round_number == 1)
        self.assertEqual(round1_roll.die, 'D8', "round1_bonus_side should reach round 1 through resolve_battle's real call chain")
        end = next(e for e in events if e.kind == EventKind.BATTLE_END)
        self.assertEqual(end.outcome, 'defender_eliminated')


class TestDigIn(unittest.TestCase):
    """Infantry's 'Dig In' trait (units.json's special_abilities): +1
    defense while defending, every round -- not just round 1, and unlike
    combat.first_round_bonuses, unconditional and entirely intrinsic to
    combat.py (no engine.py trigger determination needed). Driven by
    UnitInstance.effective_stats' `defending` flag, which is data-sourced
    from special_abilities rather than a hardcoded unit-type check."""

    def test_infantry_gets_plus_one_defense_only_while_defending(self):
        inf = make(1, 'Infantry', 'NAA')
        self.assertEqual(inf.effective_stats(UNIT_DEFS, defending=True)['defense'], 6)
        self.assertEqual(inf.effective_stats(UNIT_DEFS, defending=False)['defense'], 5, 'no Dig In while attacking')

    def test_non_infantry_unaffected_by_the_defending_flag(self):
        armor = make(1, 'Armor', 'NAA')
        self.assertEqual(armor.effective_stats(UNIT_DEFS, defending=True)['defense'], 7, "Armor has no Dig In trait")

    def test_stacks_with_promotion_and_the_round1_bonus(self):
        # base 5, +1 promotion, +1 round1_bonus, +1 Dig In = 8.
        inf = make(1, 'Infantry', 'NAA', promoted=True)
        self.assertEqual(inf.effective_stats(UNIT_DEFS, round1_bonus=True, defending=True)['defense'], 8)

    def test_applies_in_every_round_not_just_round_1(self):
        # Armor (no Dig In) attacking Infantry: a roll of 5 would hit
        # Infantry's base defense (5) but not its Dig In-boosted defense
        # (6) -- confirmed a miss across round 1, 2, AND 3, unlike
        # combat.first_round_bonuses which only ever applies in round 1.
        attacker = make(1, 'Armor', 'NAA')
        defender = make(2, 'Infantry', 'AAC')
        for round_number in (1, 2, 3):
            events = list(_fight_one_round(
                ScriptedRNG([5, 1]), round_number, [attacker], [defender], UNIT_DEFS, combat_cfg(),
                RULES['combat']['resolution_order']['land'], 0,
            ))
            attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
            self.assertFalse(attacker_roll.hit, f'round {round_number}: Dig In should apply regardless of round number')

    def test_resolve_battle_applies_dig_in_to_the_real_defending_side_only(self):
        # Infantry vs Infantry: whichever one is DEFENDING gets Dig In,
        # not both automatically and not the attacker -- exercised
        # through the real resolve_battle roll sequence rather than the
        # effective_stats unit test above.
        attacker = make(1, 'Infantry', 'NAA', hp=2)
        defender = make(2, 'Infantry', 'AAC', hp=2)
        # Attacker rolls 5 against the defender's Dig-In-boosted defense
        # (6) -- a miss. Defender then rolls 5 back against the
        # ATTACKER's plain, un-dug-in defense (5, since it's not
        # defending) -- a clean hit, killing the attacker (hp 2, damage 2).
        events = drain([attacker], [defender], 'land', ScriptedRNG([5, 5]))
        attacker_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')
        defender_roll = next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'defender')
        self.assertFalse(attacker_roll.hit, "the defender's Dig In should have blocked this")
        self.assertTrue(defender_roll.hit, "the attacker isn't defending, so it gets no Dig In of its own")
        end = next(e for e in events if e.kind == EventKind.BATTLE_END)
        self.assertEqual(end.outcome, 'attacker_eliminated')


class TestAirSuperiorityDieAdjustments(unittest.TestCase):
    """combat.air_superiority_die_adjustments: during the pre-combat
    air-superiority round only, Fighter's attack die steps up TWO sizes
    (D6 -> D10) and Bomber's is unchanged (its base D8 already is the
    design doc's "D8 attack") with its damage fixed at 2 (an absolute
    override of its normal 3, not a relative one) -- see UnitInstance.
    effective_stats' `air_superiority` parameter. Driven directly via
    _fight_one_round the same way TestFirstRoundCombatBonus drives
    round1_bonus_side."""

    def _run_round(self, round_number, attacker, defender, rolls, air_superiority):
        rng = ScriptedRNG(rolls)
        return list(_fight_one_round(
            rng, round_number, [attacker], [defender], UNIT_DEFS, combat_cfg(),
            RULES['combat']['resolution_order']['sea'], 0, air_superiority=air_superiority,
        ))

    def _attacker_roll(self, events):
        return next(e for e in events if e.kind == EventKind.UNIT_ROLL and e.side == 'attacker')

    def test_fighter_die_steps_up_two_sizes(self):
        attacker = make(1, 'Fighter', 'NAA')  # base D6
        defender = make(2, 'Cruiser', 'AAC')
        events = self._run_round(0, attacker, defender, rolls=[3, 1], air_superiority=True)
        self.assertEqual(self._attacker_roll(events).die, 'D10', "Fighter's air-superiority die is D10 (D6 stepped up twice)")

    def test_fighter_die_unaffected_outside_air_superiority(self):
        attacker = make(1, 'Fighter', 'NAA')
        defender = make(2, 'Cruiser', 'AAC')
        events = self._run_round(1, attacker, defender, rolls=[3, 1], air_superiority=False)
        self.assertEqual(self._attacker_roll(events).die, 'D6')

    def test_bomber_die_is_unchanged_and_damage_drops_to_2(self):
        attacker = make(1, 'Bomber', 'NAA')  # base D8, damage 3
        defender = make(2, 'Cruiser', 'AAC', hp=10)  # defense 7 -- a roll of 8 hits it cleanly
        events = self._run_round(0, attacker, defender, rolls=[8, 1], air_superiority=True)
        roll = self._attacker_roll(events)
        self.assertEqual(roll.die, 'D8', "Bomber's air-superiority die is its ordinary D8")
        self.assertTrue(roll.hit)
        self.assertEqual(roll.damage, 2, 'air-superiority Bomber damage is fixed at 2, not the normal 3')

    def test_bomber_normal_damage_unaffected_outside_air_superiority(self):
        attacker = make(1, 'Bomber', 'NAA')
        defender = make(2, 'Cruiser', 'AAC', hp=10)
        events = self._run_round(1, attacker, defender, rolls=[8, 1], air_superiority=False)
        roll = self._attacker_roll(events)
        self.assertEqual(roll.die, 'D8')
        self.assertTrue(roll.hit)
        self.assertEqual(roll.damage, 3)

    def test_stacks_with_promotion(self):
        # Fighter promoted: D6 -> D8 (promotion alone). Air superiority
        # steps it up two more on top: D8 -> D12.
        attacker = make(1, 'Fighter', 'NAA', promoted=True)
        defender = make(2, 'Cruiser', 'AAC')
        events = self._run_round(0, attacker, defender, rolls=[3, 1], air_superiority=True)
        self.assertEqual(self._attacker_roll(events).die, 'D12', 'promotion (D6->D8) and air superiority (D8->D12) should stack')

    def test_a_promoted_bomber_keeps_its_promotion_die_in_air_superiority(self):
        attacker = make(1, 'Bomber', 'NAA', promoted=True)  # promotion: D8 -> D10
        defender = make(2, 'Cruiser', 'AAC', hp=10)
        events = self._run_round(0, attacker, defender, rolls=[8, 1], air_superiority=True)
        roll = self._attacker_roll(events)
        self.assertEqual(roll.die, 'D10')
        self.assertEqual(roll.damage, 2)


class TestSubmarineAirInvisibility(unittest.TestCase):
    """combat.submarine_air_invisibility (units.json's 'Submerge' trait):
    a Submarine attacker never sees an Air-category unit in its target
    pool, and an Air attacker never sees a Submarine, regardless of the
    roll -- filtered out before the clean/bypass pool logic even runs,
    driven directly via _select_target the same way
    TestTargetSelectionWeighting does."""

    def test_submarine_never_targets_a_fighter_even_at_die_max(self):
        # roll == die_max (8): without the exclusion, Fighter's defense
        # (8) would be a clean hit target. With it, Fighter must never
        # be selectable at all.
        fighter = make(1, 'Fighter', 'AAC')  # defense 8
        for _ in range(200):
            target, is_hit, is_bypass = _select_target(random.Random(), 8, 8, 'Submarine', [fighter], UNIT_DEFS, 2, 1, {})
            self.assertIsNone(target, 'a lone Fighter must never be targetable by a Submarine')
            self.assertFalse(is_hit)

    def test_submarine_targets_the_non_air_unit_when_a_fighter_is_also_present(self):
        fighter = make(1, 'Fighter', 'AAC')  # defense 8 -- would clean-hit at roll 8 if not excluded
        cruiser = make(2, 'Cruiser', 'AAC')  # defense 7
        for _ in range(200):
            target, is_hit, is_bypass = _select_target(random.Random(), 8, 8, 'Submarine', [fighter, cruiser], UNIT_DEFS, 2, 1, {})
            self.assertEqual(target.unit_id, 2, 'the Fighter must be excluded from the pool entirely, leaving only the Cruiser')
            self.assertTrue(is_hit)
            self.assertFalse(is_bypass, 'Cruiser is reached via a clean hit, not the bypass')

    def test_fighter_never_targets_a_submarine(self):
        sub = make(1, 'Submarine', 'AAC')  # defense 6
        for _ in range(200):
            target, is_hit, is_bypass = _select_target(random.Random(), 8, 8, 'Fighter', [sub], UNIT_DEFS, 2, 1, {})
            self.assertIsNone(target, 'a lone Submarine must never be targetable by a Fighter')

    def test_bomber_never_targets_a_submarine_either(self):
        sub = make(1, 'Submarine', 'AAC')  # defense 6
        cruiser = make(2, 'Cruiser', 'AAC')  # defense 7
        for _ in range(200):
            target, is_hit, is_bypass = _select_target(random.Random(), 8, 8, 'Bomber', [sub, cruiser], UNIT_DEFS, 2, 1, {})
            self.assertEqual(target.unit_id, 2, 'the Submarine must be excluded from a Bomber attacker\'s pool too')

    def test_non_air_non_submarine_attackers_are_unaffected(self):
        sub = make(1, 'Submarine', 'AAC')  # defense 6
        target, is_hit, is_bypass = _select_target(random.Random(1), 6, 6, 'Cruiser', [sub], UNIT_DEFS, 2, 1, {})
        self.assertEqual(target.unit_id, 1, 'a Cruiser (not Submarine or Air) can target a Submarine normally')


class TestTransportFormInSeaBattles(unittest.TestCase):
    """rules.json combat.transport_form_in_sea_battles: a Land-category unit in
    a SEA battle is Transport cargo -- no attack, defense 6, 1 HP, no XP, and
    it dies with one hit; a survivor gets its real HP back."""

    def test_cargo_never_rolls(self):
        escort = make(1, 'Cruiser', 'NAA')
        cargo = make(2, 'Armor', 'NAA')
        foe = make(3, 'Submarine', 'AAC')
        events = drain([escort, cargo], [foe], 'sea', random.Random(1))
        rollers = {e.unit_id for e in events if e.kind == EventKind.UNIT_ROLL}
        self.assertNotIn(2, rollers)

    def test_cargo_is_sunk_by_any_hit_whatever_its_own_hp(self):
        cargo = make(1, 'Armor', 'NAA')  # 4 HP as a land unit
        foe = make(2, 'Cruiser', 'AAC')  # D10, damage well under 4? one hit still kills a 1-HP transport
        events = drain([cargo], [foe], 'sea', ScriptedRNG([10, 10, 10, 10]))
        self.assertEqual(events[-1].eliminated_attacker_ids, [1])
        self.assertLessEqual(cargo.current_hp, 0)

    def test_cargo_has_defense_6_so_a_roll_of_5_misses_it(self):
        cargo = make(1, 'Infantry', 'NAA')  # its land defense (5, +1 Dig In) is not what counts
        foe = make(2, 'Submarine', 'AAC')  # D8
        events = drain([cargo], [foe], 'sea', ScriptedRNG([5, 5, 5]))
        rolls = [e for e in events if e.kind == EventKind.UNIT_ROLL]
        self.assertTrue(rolls and all(not r.hit for r in rolls))

    def test_surviving_cargo_gets_its_hp_back_and_no_xp(self):
        cargo = make(1, 'Armor', 'NAA', hp=3)
        escort = make(2, 'Cruiser', 'NAA')
        foe = make(3, 'Submarine', 'AAC')
        drain([cargo, escort], [foe], 'sea', ScriptedRNG([1] * 20))  # nobody hits anybody
        self.assertEqual(cargo.current_hp, 3)
        self.assertEqual(cargo.xp, 0)
        self.assertFalse(cargo.in_transport_form)

    def test_land_units_fight_normally_in_a_land_battle(self):
        attacker = make(1, 'Armor', 'NAA')
        defender = make(2, 'Infantry', 'AAC')
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]))
        self.assertIn(1, {e.unit_id for e in events if e.kind == EventKind.UNIT_ROLL})
        self.assertFalse(attacker.in_transport_form)


class TestUnitStatsEvents(unittest.TestCase):
    """UNIT_STATS: per-round snapshots a battle board places units by."""

    def stats_events(self, events, phase):
        return [e for e in events if e.kind == EventKind.UNIT_STATS and e.stats_phase == phase]

    def test_one_start_and_one_end_snapshot_per_round(self):
        events = drain([make(1, 'Armor', 'NAA')], [make(2, 'Infantry', 'AAC')], 'land', ScriptedRNG([1] * 20))
        starts = self.stats_events(events, 'start')
        ends = self.stats_events(events, 'end')
        self.assertEqual([e.round_number for e in starts], [1, 2, 3])
        self.assertEqual([e.round_number for e in ends], [1, 2, 3])
        # start snapshots come before that round's first roll, end ones after its casualties
        kinds = [(e.kind, e.round_number) for e in events]
        first_roll = kinds.index((EventKind.UNIT_ROLL, 1))
        self.assertLess(events.index(starts[0]), first_roll)
        self.assertGreater(events.index(ends[0]), kinds.index((EventKind.ROUND_CASUALTIES, 1)))

    def test_snapshot_has_the_die_and_defense_each_unit_fights_with(self):
        # Infantry defending: base defense 5, +1 Dig In = 6; Armor attacking: D8, defense 7
        events = drain([make(1, 'Armor', 'NAA')], [make(2, 'Infantry', 'AAC')], 'land', ScriptedRNG([1] * 20))
        rows = {r['unit_id']: r for r in self.stats_events(events, 'start')[0].unit_stats}
        self.assertEqual((rows[1]['side'], rows[1]['die'], rows[1]['defense']), ('attacker', 'D8', 7))
        self.assertEqual((rows[2]['side'], rows[2]['die'], rows[2]['defense']), ('defender', 'D6', 6))

    def test_round_one_bonus_shows_up_in_the_round_one_snapshot_only(self):
        events = drain([make(1, 'Armor', 'NAA')], [make(2, 'Cruiser', 'AAC')], 'sea', ScriptedRNG([1] * 20),
                       round1_bonus_side='attacker')
        r1, r2 = self.stats_events(events, 'start')[:2]
        armor_r1 = next(r for r in r1.unit_stats if r['unit_id'] == 1)
        cruiser_r1 = next(r for r in r1.unit_stats if r['unit_id'] == 2)
        cruiser_r2 = next(r for r in r2.unit_stats if r['unit_id'] == 2)
        self.assertIsNone(armor_r1['die'])  # cargo in a sea battle: no attack die
        self.assertTrue(armor_r1['cargo'])
        self.assertEqual(cruiser_r1['die'], 'D10')
        self.assertEqual(cruiser_r2['die'], 'D10')

    def test_end_snapshot_reflects_damage_and_xp(self):
        attacker = make(1, 'Armor', 'NAA')
        defender = make(2, 'Infantry', 'AAC')
        events = drain([attacker], [defender], 'land', ScriptedRNG([6, 1]))
        end = self.stats_events(events, 'end')[0]
        rows = {r['unit_id']: r for r in end.unit_stats}
        self.assertLessEqual(rows[2]['hp'], 0)  # the Infantry took the hit
        self.assertGreater(rows[1]['xp'], 0)    # the Armor survived and dealt damage


if __name__ == '__main__':
    unittest.main()
