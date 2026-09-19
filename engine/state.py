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


class FactionMode(Enum):
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
    # combat.first_round_bonuses' "amphibious landing" case: True for a
    # LAND unit whose most recent combat move (movement.
    # trace_combat_move's crossed_water) touched a sea zone -- stamped by
    # GameEngine._execute_combat_moves, read by resolve_combat (only
    # meaningful there alongside has_moved_combat == True, i.e. this
    # turn's move specifically -- see that method's own comment), and
    # reset to False, like has_moved_combat, at the end of this unit's
    # owner's turn (GameEngine.advance_turn). Deliberately NOT a
    # multi-turn "has this unit ever crossed water and not walked since"
    # tracker -- the bonus is one-shot, only for the turn a purely-
    # amphibious assault actually lands, confirmed this session.
    arrived_amphibiously: bool = False
    # Transient, never serialized: True only while combat.resolve_battle has this
    # LAND unit in a SEA battle, where it is Transport cargo (see effective_stats).
    in_transport_form: bool = field(default=False, repr=False, compare=False)

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
        if self.in_transport_form:
            # A land unit in a sea battle is its Transport for the battle:
            # units.json's Transport row (defense 6, 1 HP, no attack die or
            # damage). Promotion, Dig In and the die adjustments don't apply;
            # a round-1 bonus still hardens the defense of the side that has it.
            ship = unit_defs['Transport']
            defense = ship['defense']
            if round1_bonus and defense is not None:
                defense = min(defense + 1, 10)
            return {
                'attack_die': None, 'defense': defense, 'damage': None, 'max_hp': ship['hp'],
                'category': ship['category'],
                'combat_move': ship['combat_move'], 'non_combat_move': ship['non_combat_move'],
            }
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
            'arrived_amphibiously': self.arrived_amphibiously,
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
    # combat.first_round_bonuses' "former-ally territory reclaim" case:
    # the faction code that gets the first-round attack bonus the next
    # time IT specifically attacks this territory -- set by
    # GameEngine.withdraw_from_alliance when a betrayed ally's own
    # territory is left occupied by the withdrawing faction's units.
    # Consumed (cleared) only when that named faction actually attacks
    # here, whatever the outcome -- a different faction attacking first
    # leaves it untouched, waiting for the intended recipient.
    reclaim_bonus_for: Optional[str] = None
    # combat.first_round_bonuses' "sea deploy into enemy-occupied zone"
    # case: every faction code currently owed the first-round ATTACK
    # bonus the next time IT specifically attacks this sea zone -- set by
    # GameEngine._deploy_to_sea for every non-ally who already had units
    # here when a hostile deploy landed on top of them (potentially more
    # than one faction at once, each caught equally unprepared --
    # confirmed this session). Consumed (removed from this set, not the
    # whole set cleared) only when that one named faction actually
    # attacks here, whatever the outcome; every other still-queued
    # faction's entry is untouched.
    ambush_bonus_for: set = field(default_factory=set)

    def to_dict(self):
        return {
            'territory_id': self.territory_id,
            'owner': self.owner,
            'units': [u.to_dict() for u in self.units],
            'contested_by': sorted(self.contested_by) if self.contested_by else None,
            'pending_deployment': [u.to_dict() for u in self.pending_deployment],
            'reclaim_bonus_for': self.reclaim_bonus_for,
            'ambush_bonus_for': sorted(self.ambush_bonus_for),
        }

    @staticmethod
    def from_dict(d):
        return TerritoryState(
            territory_id=d['territory_id'],
            owner=d['owner'],
            units=[UnitInstance.from_dict(u) for u in d['units']],
            contested_by=set(d['contested_by']) if d.get('contested_by') else None,
            pending_deployment=[UnitInstance.from_dict(u) for u in d['pending_deployment']],
            reclaim_bonus_for=d.get('reclaim_bonus_for'),
            ambush_bonus_for=set(d.get('ambush_bonus_for', [])),
        )


@dataclass
class FactionState:
    code: str
    mode: FactionMode
    treasury_mpc: int = 0  # MPC: the game's currency, spent in the Purchase phase, earned at Deploy + Income
    alliance: Optional[str] = None  # a shared, GameEngine-generated tag -- two factions are allied iff this is set and equal
    eliminated: bool = False
    # Incremented by GameEngine.advance_turn() each time THIS faction's own
    # turn concludes -- 0 means it hasn't had any turn yet, i.e. its
    # CURRENT turn (if active) is still its first. Consulted by
    # advance_phase() against GameState.allow_combat_moves_first_turn/
    # allow_noncombat_moves_first_turn (game_start_settings) to decide
    # whether to skip those two phases for this turn.
    turns_taken: int = 0
    # game_start_settings.can_rejoin_alliances: every faction code that
    # was a fellow member of an alliance this faction has WITHDRAWN
    # from -- maintained symmetrically (both sides updated together) by
    # GameEngine.withdraw_from_alliance. When can_rejoin_alliances is
    # False, this faction can never again share an alliance with anyone
    # in this set (checked against the WHOLE prospective alliance, not
    # just the direct inviter, at invite_to_alliance time).
    former_allies: set = field(default_factory=set)
    # Bot-only game-start settings (engine.bots.alliance_policy): a fixed
    # string ('aggressive'/'passive'/'counterweight'/'independent' and
    # 'loyal'/'opportunistic'/'treacherous' respectively) resolved ONCE at
    # setup.build_game_state time -- a 'random' input is rolled to a
    # concrete value there and only the concrete result is stored, so it
    # stays fixed for the rest of the game even though it was chosen
    # randomly. None for HUMAN/DEFENSIVE/NEUTRAL factions, which never
    # consult this policy layer at all.
    alliance_strategy: Optional[str] = None
    alliance_behavior: Optional[str] = None
    # alliance_strategy/alliance_behavior == 'variable' only: the concrete
    # value actually driving this faction's decisions right now -- 'variable'
    # itself is never consulted directly by engine.bots.alliance_policy's
    # decision functions (they always read through effective_alliance_
    # strategy/effective_alliance_behavior instead), only re-rolled to a
    # fresh concrete STRATEGIES/BEHAVIORS member (never 'variable' or
    # 'random' themselves) once at setup.build_game_state time (so there's
    # a real decision from turn 1, not None) and again at the start of
    # every one of this faction's own turns (RandomBot._maybe_reroll_
    # variable_alliance_settings) -- see alliance_policy.reroll_alliance_
    # strategy/reroll_alliance_behavior. None/unused when alliance_strategy/
    # alliance_behavior isn't 'variable'.
    current_alliance_strategy: Optional[str] = None
    current_alliance_behavior: Optional[str] = None
    # alliance_behavior == 'treacherous' only: this faction's RandomBot
    # rolls its 15%-chance "decide to withdraw" check once, at the START
    # of its own turn (take_purchase_phase), and stores the result here
    # so the SAME decision is still honored later that same turn, at the
    # Alliances phase (take_alliance_phase) -- consumed (reset to False)
    # the moment it's read there, whether or not the withdrawal actually
    # went through.
    pending_treacherous_withdrawal: bool = False

    def to_dict(self):
        return {
            'code': self.code,
            'mode': self.mode.value,
            'treasury_mpc': self.treasury_mpc,
            'alliance': self.alliance,
            'eliminated': self.eliminated,
            'turns_taken': self.turns_taken,
            'former_allies': sorted(self.former_allies),
            'alliance_strategy': self.alliance_strategy,
            'alliance_behavior': self.alliance_behavior,
            'current_alliance_strategy': self.current_alliance_strategy,
            'current_alliance_behavior': self.current_alliance_behavior,
            'pending_treacherous_withdrawal': self.pending_treacherous_withdrawal,
        }

    @staticmethod
    def from_dict(d):
        return FactionState(
            code=d['code'],
            mode=FactionMode(d['mode']),
            treasury_mpc=d['treasury_mpc'],
            alliance=d['alliance'],
            eliminated=d['eliminated'],
            turns_taken=d.get('turns_taken', 0),
            former_allies=set(d.get('former_allies', [])),
            alliance_strategy=d.get('alliance_strategy'),
            alliance_behavior=d.get('alliance_behavior'),
            current_alliance_strategy=d.get('current_alliance_strategy'),
            current_alliance_behavior=d.get('current_alliance_behavior'),
            pending_treacherous_withdrawal=d.get('pending_treacherous_withdrawal', False),
        )


@dataclass
class GameState:
    # Monotonically-increasing counter, one tick per faction-turn (not per
    # round) -- the recovery/heal rule is measured against this, not a
    # per-faction turn number. A "round" is global_turn // num_active_factions.
    global_turn: int = 0
    active_faction: Optional[str] = None
    phase: Phase = Phase.PURCHASE
    territories: dict = field(default_factory=dict)  # dict[int, TerritoryState]
    factions: dict = field(default_factory=dict)  # dict[str, FactionState]
    _next_unit_id: int = 1
    # victory.game_end_rule: set by GameEngine.process_game_end_check,
    # checked once per turn at the very end (after the Alliances phase)
    # -- true once every remaining active faction is mutually allied with
    # every other, with nobody left non-allied to keep fighting.
    game_over: bool = False
    # game_start_settings, chosen once when the game is created (see
    # setup.build_game_state's randomize_play_order/allow_combat_moves_
    # first_turn/allow_noncombat_moves_first_turn params -- randomize_play_
    # order only affects the initial construction order of `factions`
    # below, it isn't itself remembered as a flag). These two ARE
    # remembered here since GameEngine.advance_phase() consults them on
    # every single turn, for as long as the game runs: whether to skip
    # the Combat Move / Non-Combat Move phases entirely for a faction
    # currently on its own first turn (FactionState.turns_taken == 0).
    allow_combat_moves_first_turn: bool = False
    allow_noncombat_moves_first_turn: bool = True
    # game_start_settings, the alliance system (this session). Remembered
    # here, unlike randomize_play_order, since GameEngine consults them
    # on every faction's Alliances phase for as long as the game runs --
    # see setup.build_game_state's matching params and
    # engine.engine.GameEngine.invite_to_alliance/withdraw_from_alliance.
    # The CONFIGURED ceiling, chosen at setup -- but not the effective one:
    # GameEngine.invite_to_alliance never actually enforces this value
    # directly, only GameEngine._effective_max_alliance_size(), which is
    # this further capped by (len(active_factions()) - 1), recomputed
    # fresh on every invite -- an alliance can never include literally
    # every faction still in the game. That shrinks as factions are
    # eliminated over the course of the game, progressively restricting
    # what NEW alliances can form or grow into, but never dissolves an
    # existing alliance that's already larger than the current value.
    max_alliance_size: int = 2
    can_withdraw_from_alliances: bool = True
    can_rejoin_alliances: bool = False
    # GameEngine._new_alliance_tag()'s counter -- purely an internal
    # correlation id for FactionState.alliance, never itself
    # game-meaningful (not shown to a player, not compared to anything
    # but itself).
    _next_alliance_id: int = 1

    def new_unit_id(self):
        uid = self._next_unit_id
        self._next_unit_id += 1
        return uid

    def active_factions(self):
        """Faction codes with mode HUMAN or BOT, and not yet eliminated
        (victory.elimination_rule) -- the ones that actually take turns.
        Order is insertion order of `factions`. Every phase method in
        engine.GameEngine gates on this, and combat.recovery_rule's
        num_factions is len(this) -- so excluding an eliminated faction
        here is what actually enforces "gets no more turns," not
        anything phase-specific."""
        return [code for code, f in self.factions.items() if f.mode in (FactionMode.HUMAN, FactionMode.BOT) and not f.eliminated]

    def to_dict(self):
        return {
            'global_turn': self.global_turn,
            'active_faction': self.active_faction,
            'phase': self.phase.value,
            'territories': {str(tid): t.to_dict() for tid, t in self.territories.items()},
            'factions': {code: f.to_dict() for code, f in self.factions.items()},
            'next_unit_id': self._next_unit_id,
            'game_over': self.game_over,
            'allow_combat_moves_first_turn': self.allow_combat_moves_first_turn,
            'allow_noncombat_moves_first_turn': self.allow_noncombat_moves_first_turn,
            'max_alliance_size': self.max_alliance_size,
            'can_withdraw_from_alliances': self.can_withdraw_from_alliances,
            'can_rejoin_alliances': self.can_rejoin_alliances,
            'next_alliance_id': self._next_alliance_id,
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
            allow_combat_moves_first_turn=d.get('allow_combat_moves_first_turn', False),
            allow_noncombat_moves_first_turn=d.get('allow_noncombat_moves_first_turn', True),
            max_alliance_size=d.get('max_alliance_size', 2),
            can_withdraw_from_alliances=d.get('can_withdraw_from_alliances', True),
            can_rejoin_alliances=d.get('can_rejoin_alliances', False),
            _next_alliance_id=d.get('next_alliance_id', 1),
        )
