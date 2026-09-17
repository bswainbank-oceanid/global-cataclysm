"""
Battle resolution.

resolve_battle() is a generator, not a plain function, so the exact same
resolution logic drives both auto-play and an interactive step-through UI:
the player never makes a choice in combat, so "interactive" is purely
about pacing/reveal, not decisions -- both modes replay identically, they
just differ in how fast the caller consumes events.
  - Auto-play: drain it -- `events = list(resolve_battle(...))`, or a
    `for event in resolve_battle(...): pass` loop.
  - Interactive: call `next(gen)` once per user action ("roll"), and
    render each BattleEvent as it comes -- one event per unit's single
    die roll, plus round/air-superiority/promotion/battle-end markers.

Mutates the UnitInstance objects it's given in place (current_hp, xp,
promoted, last_combat_global_turn) -- these are the same persistent
service-record objects the caller holds elsewhere (TerritoryState.units),
not copies. It does NOT remove dead units from any territory or apply
capture/contested-territory consequences -- that's the caller's job
(engine.py, once built), working from the BATTLE_END event's survivor
lists. This keeps combat resolution testable in isolation from the rest
of the engine, per the build plan.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

DIE_MAX = {'D6': 6, 'D8': 8, 'D10': 10, 'D12': 12}


class EventKind(Enum):
    AIR_SUPERIORITY_START = 'AIR_SUPERIORITY_START'
    ROUND_START = 'ROUND_START'
    UNIT_ROLL = 'UNIT_ROLL'
    ROUND_CASUALTIES = 'ROUND_CASUALTIES'
    PROMOTION = 'PROMOTION'
    BATTLE_END = 'BATTLE_END'


@dataclass
class BattleEvent:
    kind: EventKind
    round_number: int  # 0 = air superiority round, 1-3 = main combat rounds

    # UNIT_ROLL
    side: Optional[str] = None  # 'attacker' | 'defender'
    unit_id: Optional[int] = None
    unit_type: Optional[str] = None
    die: Optional[str] = None
    roll: Optional[int] = None
    hit: Optional[bool] = None
    bypass_hit: Optional[bool] = None  # True if this hit only landed via the max-die bypass (defense > roll, half damage) -- False for an ordinary hit, None on a miss
    target_unit_id: Optional[int] = None  # set even on a miss, for display -- see module docstring / target selection
    damage: Optional[int] = None
    target_hp_after: Optional[int] = None

    # ROUND_CASUALTIES
    attacker_losses: Optional[list] = None  # unit_ids removed this round
    defender_losses: Optional[list] = None

    # PROMOTION
    promoted_unit_id: Optional[int] = None
    promoted_side: Optional[str] = None

    # BATTLE_END
    outcome: Optional[str] = None  # 'attacker_eliminated' | 'defender_eliminated' | 'contested' | 'mutual_elimination'
    surviving_attacker_ids: Optional[list] = None
    surviving_defender_ids: Optional[list] = None
    eliminated_attacker_ids: Optional[list] = None
    eliminated_defender_ids: Optional[list] = None


@dataclass
class BattleResult:
    """Convenience summary of a fully-drained resolve_battle() run --
    build with `BattleResult.from_events(events)`, or just watch for the
    BATTLE_END event yourself if you're consuming the generator directly."""
    outcome: str
    rounds_fought: int
    surviving_attacker_ids: list = field(default_factory=list)
    surviving_defender_ids: list = field(default_factory=list)
    eliminated_attacker_ids: list = field(default_factory=list)
    eliminated_defender_ids: list = field(default_factory=list)

    @staticmethod
    def from_events(events):
        rounds_fought = max((e.round_number for e in events), default=0)
        end = next(e for e in reversed(events) if e.kind == EventKind.BATTLE_END)
        return BattleResult(
            outcome=end.outcome,
            rounds_fought=rounds_fought,
            surviving_attacker_ids=end.surviving_attacker_ids,
            surviving_defender_ids=end.surviving_defender_ids,
            eliminated_attacker_ids=end.eliminated_attacker_ids,
            eliminated_defender_ids=end.eliminated_defender_ids,
        )


def _alive(units):
    return [u for u in units if u.current_hp > 0]


def _select_target(rng, roll, die_max, attacker_type, enemies, unit_defs, same_type_weight, bomber_weight, pending_damage,
                    enemy_round1_bonus=False, enemies_are_defenders=False):
    """Returns (target_or_None, is_hit, is_bypass_hit). `enemy_round1_bonus`:
    True if the enemies' side qualifies for one of combat.
    first_round_bonuses this round -- their defense (used below for both
    the clean-pool and bypass-tier checks) reflects it, same as an
    existing promotion would. `enemies_are_defenders`: True if `enemies`
    is this battle's actual defending side (i.e. this call is the
    attacker rolling) -- their defense also reflects Dig In if they have
    it, for every round, not just round 1 (see UnitInstance.
    effective_stats' `defending` parameter). `pending_damage`
    maps unit_id -> damage already applied to it earlier in THIS side's
    roll-through this round (not yet subtracted from current_hp) -- a
    unit whose pending damage has already reduced it to 0 or below is
    excluded from targeting, even though it's still nominally "alive"
    until round-end casualty removal.

    A "clean" hit (defense <= roll) is always preferred when one is
    available, at full damage -- the max-die-always-hits bypass (which
    can reach a target whose defense exceeds the roll, at half damage)
    only kicks in once no clean target remains. This means a max roll
    against a roster that still has a normally-reachable target is just
    an ordinary full-damage hit; the half-damage bypass is the cost of
    reaching an otherwise-unhittable target specifically, not a tax on
    rolling well.

    When there's no clean target, the pool (whether this ends up a
    bypass hit or a miss) is built the same way either way: only the
    NEXT-highest defense tier above the roll -- e.g. a roll of 6 with no
    clean target, facing a defense-7 Armor, a defense-7 Mechanized
    Infantry, and a defense-8 Fighter standing, only puts the two
    defense-7 units in the pool; the defense-8 Fighter is excluded
    entirely, even on a bypass hit. Same-type weighting still applies
    within that narrower pool. Whether it's actually a hit (bypass, half
    damage) or a miss (0 damage, targeted for display only) depends
    purely on whether roll == die_max."""
    standing = [e for e in enemies if e.current_hp - pending_damage.get(e.unit_id, 0) > 0]
    if not standing:
        return None, False, False

    defense_of = lambda e: e.effective_stats(unit_defs, round1_bonus=enemy_round1_bonus, defending=enemies_are_defenders)['defense']
    clean_pool = [e for e in standing if defense_of(e) <= roll]

    if clean_pool:
        pool, is_hit, is_bypass = clean_pool, True, False
    else:
        min_defense = min(defense_of(e) for e in standing)
        pool = [e for e in standing if defense_of(e) == min_defense]
        if roll == die_max:
            is_hit, is_bypass = True, True
        else:
            is_hit, is_bypass = False, False

    weights = [same_type_weight if (attacker_type != 'Bomber' and e.unit_type == attacker_type) else 1 for e in pool]
    target = rng.choices(pool, weights=weights, k=1)[0]
    return target, is_hit, is_bypass


def _resolution_sequence(units, unit_defs, type_order, round1_bonus=False):
    """Units grouped by type per `type_order`, then by attack die size
    ascending within a type (promotions, and now a round-1 combat bonus,
    can give same-type units different dice)."""
    def sort_key(u):
        stats = u.effective_stats(unit_defs, round1_bonus=round1_bonus)
        die = stats['attack_die']
        die_rank = list(DIE_MAX).index(die) if die in DIE_MAX else -1
        type_rank = type_order.index(u.unit_type) if u.unit_type in type_order else len(type_order)
        return (type_rank, die_rank)
    return sorted([u for u in units if u.effective_stats(unit_defs, round1_bonus=round1_bonus)['attack_die']], key=sort_key)


def _roll_side(rng, side_label, acting_units, enemy_units, unit_defs, target_cfg, round_number, current_global_turn,
                acting_round1_bonus=False, enemy_round1_bonus=False, enemies_are_defenders=False):
    """Yields one UNIT_ROLL event per acting unit's die roll, applying
    damage progressively into a local pending_damage tally (not yet
    subtracted from real current_hp -- that happens for both sides
    together after both have rolled, in _fight_one_round, which
    reconstructs the tally from these events rather than relying on a
    generator return value). Also stamps last_combat_global_turn on every
    acting unit that rolls, since it took part in a round of combat
    regardless of hit/miss.

    acting_round1_bonus / enemy_round1_bonus: whether this side, and the
    side it's rolling against, each currently qualify for one of
    combat.first_round_bonuses (only ever true together with round_number
    == 1 -- see _fight_one_round, which computes these). enemies_are_defenders:
    forwarded to _select_target for the Dig In check -- true for every
    round (not just round 1) whenever `enemy_units` is this battle's
    actual defending side."""
    pending_damage = {}
    for unit in acting_units:
        unit.last_combat_global_turn = current_global_turn
        stats = unit.effective_stats(unit_defs, round1_bonus=acting_round1_bonus)
        die = stats['attack_die']
        die_max = DIE_MAX[die]
        roll = rng.randint(1, die_max)
        target, is_hit, is_bypass = _select_target(
            rng, roll, die_max, unit.unit_type, enemy_units, unit_defs,
            target_cfg['same_type_weight'], target_cfg['bomber_attacker_weight'], pending_damage,
            enemies_are_defenders=enemies_are_defenders,
            enemy_round1_bonus=enemy_round1_bonus,
        )
        damage = 0
        target_hp_after = None
        if is_hit and target is not None:
            damage = stats['damage'] // 2 if is_bypass else stats['damage']
            pending_damage[target.unit_id] = pending_damage.get(target.unit_id, 0) + damage
            target_hp_after = target.current_hp - pending_damage[target.unit_id]
        yield BattleEvent(
            kind=EventKind.UNIT_ROLL, round_number=round_number, side=side_label,
            unit_id=unit.unit_id, unit_type=unit.unit_type, die=die, roll=roll, hit=is_hit,
            bypass_hit=is_bypass if is_hit else None,
            target_unit_id=target.unit_id if target else None, damage=damage, target_hp_after=target_hp_after,
        )


def _apply_xp_and_check_promotions(round_number, attackers_before, defenders_before,
                                    attacker_hits, defender_hits, killed_by, promotion_cfg, unit_defs):
    """Awards XP (survive the round: +1 to every unit still alive after
    this round's casualties; deal damage: +1 to any unit that landed at
    least one hit; eliminate a promoted unit: +1 to whichever unit's hit
    was the killing blow, credited via `killed_by`), then promotes any
    unit crossing the XP threshold, healing +1 HP into it immediately.
    Yields a PROMOTION event per unit promoted."""
    xp_required = promotion_cfg['xp_required']
    for side_label, units_before, hit_ids in (('attacker', attackers_before, attacker_hits), ('defender', defenders_before, defender_hits)):
        for unit in units_before:
            if unit.current_hp <= 0:
                continue  # eliminated this round -- no XP
            unit.xp += 1  # survived the round
            if unit.unit_id in hit_ids:
                unit.xp += 1  # dealt damage
    for killer_id, victim in killed_by.items():
        killer = next((u for u in attackers_before + defenders_before if u.unit_id == killer_id and u.current_hp > 0), None)
        if killer is not None and victim.promoted:
            killer.xp += 1

    for unit, side_label in [(u, 'attacker') for u in attackers_before if u.current_hp > 0] + \
                             [(u, 'defender') for u in defenders_before if u.current_hp > 0]:
        if not unit.promoted and unit.xp >= xp_required:
            unit.promoted = True
            unit.current_hp += 1  # promotion grants +1 max HP; heal it in immediately
            yield BattleEvent(kind=EventKind.PROMOTION, round_number=round_number,
                               promoted_unit_id=unit.unit_id, promoted_side=side_label)


def _fight_one_round(rng, round_number, attackers, defenders, unit_defs, combat_cfg, resolution_order, current_global_turn,
                      round1_bonus_side=None):
    """One full round (or the air-superiority round): attacker's whole
    ordered roll sequence, then defender's, then both sides' casualties
    are removed together. Yields UNIT_ROLL events (from both sides),
    then a ROUND_CASUALTIES event, then any PROMOTION events.

    round1_bonus_side: None | 'attacker' | 'defender' -- which side (if
    any) qualifies for one of combat.first_round_bonuses in THIS battle
    (amphibious landing favors the defender; a sea-deploy surprise favors
    the attacker; see rules.json -- determining which case applies, if
    any, is the caller's job, not this module's). It only ever actually
    applies when round_number == 1 -- passing it in for the air-
    superiority round (round_number 0) or a later round (2, 3) is
    harmless, since the check below excludes those rounds regardless."""
    target_cfg = combat_cfg['target_selection']
    attacker_bonus = round1_bonus_side == 'attacker' and round_number == 1
    defender_bonus = round1_bonus_side == 'defender' and round_number == 1
    attacker_order = _resolution_sequence(attackers, unit_defs, resolution_order, round1_bonus=attacker_bonus)
    defender_order = _resolution_sequence(defenders, unit_defs, resolution_order, round1_bonus=defender_bonus)

    attacker_hit_ids = set()
    defender_hit_ids = set()
    killed_by = {}  # unit_id of the killer -> victim UnitInstance, for the promoted-victim XP bonus
    defenders_by_id = {u.unit_id: u for u in defenders}
    attackers_by_id = {u.unit_id: u for u in attackers}

    def run_side(side_label, acting, acting_bonus, enemies, enemies_bonus, enemies_by_id, hit_ids, enemies_are_defenders):
        pending = {}
        for event in _roll_side(rng, side_label, acting, enemies, unit_defs, target_cfg, round_number, current_global_turn,
                                 acting_round1_bonus=acting_bonus, enemy_round1_bonus=enemies_bonus,
                                 enemies_are_defenders=enemies_are_defenders):
            yield event
            if event.hit:
                hit_ids.add(event.unit_id)
                pending[event.target_unit_id] = pending.get(event.target_unit_id, 0) + event.damage
                if event.target_hp_after is not None and event.target_hp_after <= 0:
                    killed_by[event.unit_id] = enemies_by_id[event.target_unit_id]
        return pending

    # The attacker's enemies (defenders) ARE this battle's defending side
    # -- Dig In applies. The defender's enemies (attackers) are not.
    attacker_pending = yield from run_side('attacker', attacker_order, attacker_bonus, defenders, defender_bonus, defenders_by_id, attacker_hit_ids, enemies_are_defenders=True)
    defender_pending = yield from run_side('defender', defender_order, defender_bonus, attackers, attacker_bonus, attackers_by_id, defender_hit_ids, enemies_are_defenders=False)

    for unit_id, dmg in attacker_pending.items():
        defenders_by_id[unit_id].current_hp -= dmg
    for unit_id, dmg in defender_pending.items():
        attackers_by_id[unit_id].current_hp -= dmg

    attacker_losses = [u.unit_id for u in attackers if u.current_hp <= 0]
    defender_losses = [u.unit_id for u in defenders if u.current_hp <= 0]
    yield BattleEvent(kind=EventKind.ROUND_CASUALTIES, round_number=round_number,
                       attacker_losses=attacker_losses, defender_losses=defender_losses)

    yield from _apply_xp_and_check_promotions(
        round_number, attackers, defenders, attacker_hit_ids, defender_hit_ids, killed_by,
        combat_cfg['_promotion_cfg'], unit_defs,
    )


def _has_air(units, unit_defs):
    return any(unit_defs[u.unit_type]['category'] == 'Air' for u in units)


def _has_fighter(units):
    return any(u.unit_type == 'Fighter' for u in units)


def resolve_battle(attacker_units, defender_units, battle_type, rng, current_global_turn, unit_defs, rules,
                    round1_bonus_side=None):
    """attacker_units / defender_units: list[UnitInstance] (mutated in
    place). battle_type: 'land' | 'sea'. rng: a random.Random instance
    (seed it for deterministic tests/replays). current_global_turn: the
    GameState.global_turn this battle is happening on, stamped onto every
    participating unit's last_combat_global_turn. unit_defs: engine.data.units().
    rules: engine.data.rules() (reads combat.resolution_order,
    combat.target_selection, combat.air_superiority_trigger, promotion.*).

    round1_bonus_side: None | 'attacker' | 'defender' -- see
    combat.first_round_bonuses in rules.json for the three cases this
    covers (amphibious landing, sea-deploy surprise, former-ally
    reclaim). Whether this battle actually qualifies for one, and for
    which side, is decided by the caller (engine.py, not yet built) from
    movement/deploy history this module has no visibility into -- combat.py
    only knows how to apply the bonus once told who gets it. It's only
    ever applied in round 1 of main combat, never the air-superiority
    round.

    A generator yielding BattleEvent -- drain it for auto-play, or step
    it with next() for an interactive reveal. The final event is always
    BATTLE_END."""
    combat_cfg = dict(rules['combat'])
    combat_cfg['_promotion_cfg'] = rules['promotion']
    resolution_order = combat_cfg['resolution_order'][battle_type]

    attackers = list(attacker_units)
    defenders = list(defender_units)

    if _has_air(attackers, unit_defs) and _has_air(defenders, unit_defs) and (_has_fighter(attackers) or _has_fighter(defenders)):
        yield BattleEvent(kind=EventKind.AIR_SUPERIORITY_START, round_number=0)
        air_attackers = [u for u in attackers if unit_defs[u.unit_type]['category'] == 'Air']
        air_defenders = [u for u in defenders if unit_defs[u.unit_type]['category'] == 'Air']
        yield from _fight_one_round(rng, 0, air_attackers, air_defenders, unit_defs, combat_cfg, resolution_order, current_global_turn)
        attackers = _alive(attackers)
        defenders = _alive(defenders)

    round_number = 0
    for round_number in range(1, combat_cfg['rounds_per_battle'] + 1):
        if not attackers or not defenders:
            break
        yield BattleEvent(kind=EventKind.ROUND_START, round_number=round_number)
        yield from _fight_one_round(rng, round_number, attackers, defenders, unit_defs, combat_cfg, resolution_order, current_global_turn,
                                     round1_bonus_side=round1_bonus_side)
        attackers = _alive(attackers)
        defenders = _alive(defenders)

    if not attackers and not defenders:
        outcome = 'mutual_elimination'  # both sides wiped out the same round -- no survivors on either side
    elif not defenders:
        outcome = 'defender_eliminated'
    elif not attackers:
        outcome = 'attacker_eliminated'
    else:
        outcome = 'contested'

    all_attacker_ids = [u.unit_id for u in attacker_units]
    all_defender_ids = [u.unit_id for u in defender_units]
    surviving_attacker_ids = [u.unit_id for u in attackers]
    surviving_defender_ids = [u.unit_id for u in defenders]
    yield BattleEvent(
        kind=EventKind.BATTLE_END, round_number=round_number, outcome=outcome,
        surviving_attacker_ids=surviving_attacker_ids,
        surviving_defender_ids=surviving_defender_ids,
        eliminated_attacker_ids=[uid for uid in all_attacker_ids if uid not in surviving_attacker_ids],
        eliminated_defender_ids=[uid for uid in all_defender_ids if uid not in surviving_defender_ids],
    )
