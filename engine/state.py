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

    def effective_stats(self, unit_defs):
        """unit_defs: engine.data.units() (or an equivalent test fixture).
        Returns the unit's current attack_die/defense/hp/damage after
        applying the promotion bonus (die up one size max D12, +1
        defense max 10, +1 HP) if promoted. cost/sc_cost/moves are not
        affected by promotion and are returned as-is from unit_defs."""
        base = unit_defs[self.unit_type]
        die = base['attack_die']
        defense = base['defense']
        max_hp = base['hp']
        if self.promoted:
            if die is not None:
                die = step_up_die(die)
            if defense is not None:
                defense = min(defense + 1, 10)
            max_hp = max_hp + 1
        return {
            'attack_die': die,
            'defense': defense,
            'damage': base['damage'],
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
    treasury_ipc: int = 0
    alliance: Optional[str] = None  # stub, unused in v1 (strict free-for-all)
    eliminated: bool = False

    def to_dict(self):
        return {
            'code': self.code,
            'mode': self.mode.value,
            'treasury_ipc': self.treasury_ipc,
            'alliance': self.alliance,
            'eliminated': self.eliminated,
        }

    @staticmethod
    def from_dict(d):
        return FactionState(
            code=d['code'],
            mode=PowerMode(d['mode']),
            treasury_ipc=d['treasury_ipc'],
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

    def new_unit_id(self):
        uid = self._next_unit_id
        self._next_unit_id += 1
        return uid

    def active_powers(self):
        """Faction codes with mode HUMAN or BOT -- the ones that actually
        take turns. Order is insertion order of `factions`."""
        return [code for code, f in self.factions.items() if f.mode in (PowerMode.HUMAN, PowerMode.BOT)]

    def to_dict(self):
        return {
            'global_turn': self.global_turn,
            'active_faction': self.active_faction,
            'phase': self.phase.value,
            'territories': {str(tid): t.to_dict() for tid, t in self.territories.items()},
            'factions': {code: f.to_dict() for code, f in self.factions.items()},
            'next_unit_id': self._next_unit_id,
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
        )
