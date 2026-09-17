"""
In-memory game state -- dataclasses, not a database. GameState is the
whole board at a point in time; everything here is plain data plus small
helpers, with to_dict()/from_dict() for JSON round-tripping (save files,
tests) rather than any persistence layer of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PowerMode(Enum):
    HUMAN = 'HUMAN'
    BOT = 'BOT'
    DEFENSIVE = 'DEFENSIVE'
    NEUTRAL = 'NEUTRAL'


class Phase(Enum):
    PURCHASE = 'PURCHASE'
    COMBAT_MOVE = 'COMBAT_MOVE'
    COMBAT_RESOLUTION = 'COMBAT_RESOLUTION'
    NONCOMBAT_MOVE = 'NONCOMBAT_MOVE'
    CAPTURE = 'CAPTURE'
    DEPLOY_INCOME = 'DEPLOY_INCOME'
    ALLIANCES = 'ALLIANCES'


# Attack die progression a promotion steps up through, capped at the last entry.
DIE_SIZES = ['D6', 'D8', 'D10', 'D12']


def step_up_die(die):
    if die not in DIE_SIZES:
        return die
    idx = DIE_SIZES.index(die)
    return DIE_SIZES[min(idx + 1, len(DIE_SIZES) - 1)]


def step_down_die(die):
    if die not in DIE_SIZES:
        return die
    idx = DIE_SIZES.index(die)
    return DIE_SIZES[max(idx - 1, 0)]


def _has_dig_in(base):
    """True if this unit type's data (units.json's special_abilities list)
    carries the 'Dig In' trait -- currently Infantry only, but driven by
    data rather than a hardcoded unit_type check so any future unit type
    tagged the same way picks it up automatically."""
    return any(a.startswith('Dig In') for a in base.get('special_abilities', []))


@dataclass
class UnitInstance:
    """An individual, persistent service record -- not an anonymous stack
    count. XP, promotion, and current damage all live on this specific
    unit and persist across turns and battles."""
    unit_id: int
    unit_type: str
    owner: str
    current_hp: int
    xp: int = 0
    promoted: bool = False
    has_moved_combat: bool = False
    has_moved_noncombat: bool = False
    last_combat_global_turn: Optional[int] = None
    # unit_id of the Transport carrying this unit, if it's a land unit
    # currently riding one across a sea zone; None otherwise.
    transported_by: Optional[int] = None
    # unit_id of the Aircraft Carrier this air unit departed from on its
    # most recent COMBAT move, if that carrier's sea zone was its
    # origin -- None if it combat-moved from land, or hasn't combat-
    # moved this turn. Scoped narrowly to feeding
    # GameEngine.process_return_to_base (a carrier can relocate mid-turn
    # via its own move, so a returning plane needs to track WHICH
    # carrier, not a fixed location, to find it "wherever it is" after
    # combat) -- NOT a fully general "current home carrier" tracker for
    # every move a plane makes; see combat_move_origin below and
    # rules.json's movement.carrier_air_operations for what's covered
    # and what (ride-along outside this specific return trip, chaining)
    # still isn't. Cleared once process_return_to_base consumes it.
    based_on_carrier: Optional[int] = None
    # Territory_id this air unit combat-moved FROM, set alongside
    # based_on_carrier on every combat move (regardless of whether the
    # origin had a carrier) -- the return_to_base fallback target when
    # there's no carrier to track, or that carrier didn't survive.
    # Cleared once process_return_to_base consumes it.
    combat_move_origin: Optional[int] = None
    # The LAND territory whose deploy capacity/cost actually paid for
    # this unit (engine.GameEngine.confirm_purchases sets this) -- for a
    # direct land purchase this is just the deploy target itself, but
    # for a sea deployment drawn from multiple adjacent territories via
    # spillover (purchase.multi_adjacent_allocation_order), different
    # units from the very same order can have different values here.
    # Needed at Deploy + Income for purchase.carrierless_air_deploy_fallback
    # (which land space to fall back to) and
    # purchase.contested_purchase_lost_during_turn_fallback (both
    # reference "the purchasing land space").
    purchased_at: Optional[int] = None

    def effective_stats(self, unit_defs, round1_bonus=False, defending=False, air_superiority=False):
        """unit_defs: engine.data.units() (or an equivalent test fixture).
        Returns the unit's current attack_die/defense/hp/damage after
        applying the promotion bonus (die up one size max D12, +1
        defense max 10, +1 HP) if promoted. cost/sc_cost/moves are not
        affected by promotion and are returned as-is from unit_defs.

        round1_bonus: applies one of combat.first_round_bonuses' three
        situational bonuses (amphibious landing / sea-deploy surprise /
        former-ally reclaim) -- same die-up/+1-defense transform as
        promotion, stacking additively with an existing promotion (a
        promoted unit that also qualifies steps its die up twice and
        gets +2 defense, still capped at D12/10), but grants no HP and
        is never persisted here -- the caller (combat.py) re-derives it
        fresh every time it's relevant (round 1 of one specific battle
        only), it's not a property of the unit itself.

        defending: True if this unit is on the defending side of the
        battle this call is for. Unlike round1_bonus, this isn't
        situational -- it's just "is this unit currently defending,"
        true or false for the unit's entire time in a battle (all
        rounds, not just round 1) -- and unlike promotion, it isn't
        persisted on the unit either, since a unit that's attacking in
        one battle can be defending in the next. Units with the 'Dig In'
        trait (units.json's special_abilities -- Infantry, currently)
        get +1 defense (capped at 10) while defending, stacking
        additively with promotion and round1_bonus same as those do with
        each other.

        air_superiority: True only for the pre-combat air-superiority
        round -- Fighter steps its attack die up one size (stacking on
        top of any promotion, same as round1_bonus would); Bomber steps
        its attack die DOWN one size instead and its damage is fixed at
        2 (an absolute override, not a relative one -- Bomber's normal
        damage, 4, isn't touched by promotion either, so there's nothing
        to stack against). Every other unit type is unaffected -- this
        never touches defense, so it plays no part in a target's
        defense_of computation, only the acting unit's own roll."""
        base = unit_defs[self.unit_type]
        die = base['attack_die']
        defense = base['defense']
        damage = base['damage']
        max_hp = base['hp']
        if self.promoted:
            if die is not None:
                die = step_up_die(die)
            if defense is not None:
                defense = min(defense + 1, 10)
            max_hp = max_hp + 1
        if round1_bonus:
            if die is not None:
                die = step_up_die(die)
            if defense is not None:
                defense = min(defense + 1, 10)
        if defending and defense is not None and _has_dig_in(base):
            defense = min(defense + 1, 10)
        if air_superiority:
            if self.unit_type == 'Fighter' and die is not None:
                die = step_up_die(die)
            elif self.unit_type == 'Bomber':
                if die is not None:
                    die = step_down_die(die)
                damage = 2
        return {
            'attack_die': die,
            'defense': defense,
            'damage': damage,
            'max_hp': max_hp,
            'category': base['category'],
            'combat_move': base['combat_move'],
            'non_combat_move': base['non_combat_move'],
        }

    def to_dict(self):
        return {
            'unit_id': self.unit_id,
            'unit_type': self.unit_type,
            'owner': self.owner,
            'current_hp': self.current_hp,
            'xp': self.xp,
            'promoted': self.promoted,
            'has_moved_combat': self.has_moved_combat,
            'has_moved_noncombat': self.has_moved_noncombat,
            'last_combat_global_turn': self.last_combat_global_turn,
            'transported_by': self.transported_by,
            'based_on_carrier': self.based_on_carrier,
            'combat_move_origin': self.combat_move_origin,
            'purchased_at': self.purchased_at,
        }

    @staticmethod
    def from_dict(d):
        return UnitInstance(**d)


@dataclass
class TerritoryState:
    territory_id: int
    owner: Optional[str] = None  # faction code; None for Neutral/unowned sea
    units: list = field(default_factory=list)  # list[UnitInstance]
    # Faction codes currently contesting this territory (attackers whose
    # combat move ended here but combat hasn't fully resolved/eliminated
    # one side), or None if uncontested.
    contested_by: Optional[set] = None
    # Units bought this turn's purchase phase, not yet on the board --
    # placed onto `units` during the deploy phase.
    pending_deployment: list = field(default_factory=list)  # list[UnitInstance]

    def to_dict(self):
        return {
            'territory_id': self.territory_id,
            'owner': self.owner,
            'units': [u.to_dict() for u in self.units],
            'contested_by': sorted(self.contested_by) if self.contested_by else None,
            'pending_deployment': [u.to_dict() for u in self.pending_deployment],
        }

    @staticmethod
    def from_dict(d):
        return TerritoryState(
            territory_id=d['territory_id'],
            owner=d['owner'],
            units=[UnitInstance.from_dict(u) for u in d['units']],
            contested_by=set(d['contested_by']) if d.get('contested_by') else None,
            pending_deployment=[UnitInstance.from_dict(u) for u in d['pending_deployment']],
        )


@dataclass
class FactionState:
    code: str
    mode: PowerMode
    treasury_mpc: int = 0  # MPC: the game's currency, spent in the Purchase phase, earned at Deploy + Income
    alliance: Optional[str] = None  # stub, unused in v1 (strict free-for-all)
    eliminated: bool = False

    def to_dict(self):
        return {
            'code': self.code,
            'mode': self.mode.value,
            'treasury_mpc': self.treasury_mpc,
            'alliance': self.alliance,
            'eliminated': self.eliminated,
        }

    @staticmethod
    def from_dict(d):
        return FactionState(
            code=d['code'],
            mode=PowerMode(d['mode']),
            treasury_mpc=d['treasury_mpc'],
            alliance=d['alliance'],
            eliminated=d['eliminated'],
        )


@dataclass
class GameState:
    # Monotonically-increasing counter, one tick per faction-turn (not per
    # round) -- the recovery/heal rule is measured against this, not a
    # per-faction turn number. A "round" is global_turn // num_active_powers.
    global_turn: int = 0
    active_faction: Optional[str] = None
    phase: Phase = Phase.PURCHASE
    territories: dict = field(default_factory=dict)  # dict[int, TerritoryState]
    factions: dict = field(default_factory=dict)  # dict[str, FactionState]
    _next_unit_id: int = 1
    # victory.game_end_rule: set by GameEngine.process_game_end_check,
    # checked once per turn at the very end (after the Alliances phase)
    # -- true once every remaining active power is mutually allied with
    # every other, with nobody left non-allied to keep fighting.
    game_over: bool = False

    def new_unit_id(self):
        uid = self._next_unit_id
        self._next_unit_id += 1
        return uid

    def active_powers(self):
        """Faction codes with mode HUMAN or BOT, and not yet eliminated
        (victory.elimination_rule) -- the ones that actually take turns.
        Order is insertion order of `factions`. Every phase method in
        engine.GameEngine gates on this, and combat.recovery_rule's
        num_powers is len(this) -- so excluding an eliminated faction
        here is what actually enforces "gets no more turns," not
        anything phase-specific."""
        return [code for code, f in self.factions.items() if f.mode in (PowerMode.HUMAN, PowerMode.BOT) and not f.eliminated]

    def to_dict(self):
        return {
            'global_turn': self.global_turn,
            'active_faction': self.active_faction,
            'phase': self.phase.value,
            'territories': {str(tid): t.to_dict() for tid, t in self.territories.items()},
            'factions': {code: f.to_dict() for code, f in self.factions.items()},
            'next_unit_id': self._next_unit_id,
            'game_over': self.game_over,
        }

    @staticmethod
    def from_dict(d):
        return GameState(
            global_turn=d['global_turn'],
            active_faction=d['active_faction'],
            phase=Phase(d['phase']),
            territories={int(k): TerritoryState.from_dict(v) for k, v in d['territories'].items()},
            factions={k: FactionState.from_dict(v) for k, v in d['factions'].items()},
            _next_unit_id=d['next_unit_id'],
            game_over=d.get('game_over', False),
        )
