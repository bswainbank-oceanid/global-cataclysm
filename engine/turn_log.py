"""
TurnLog: an optional, append-only, ORDERED narration of a game, for a
caller (server/session.py, in practice) that wants to replay what
happened -- a bot's whole turn, run start-to-finish by the engine in one
go rather than interactively, or a human's own Combat Resolution (no
player choice in HOW it plays out, but they still submitted the attack
and want to watch it land) -- back to a human audience at whatever pace
they choose. Distinct from stats.GameStats, which is a whole-game
AGGREGATE report (running tallies, built for an end-of-game summary) --
this instead keeps every individual roll, order, and outcome in the
exact order it happened, never summarized or discarded. Like GameStats,
TurnLog is purely an observer -- GameEngine, when constructed with a
TurnLog instance (the `turn_log` kwarg), appends to it as each fact
becomes known; nothing here drives or affects the game itself, and a
GameEngine with no turn_log sink behaves identically to before this
module existed.

Never reset by GameEngine itself -- events simply accumulate for the
life of the TurnLog. A caller wanting "just this stretch of play" (one
bot's turn, or one Combat Resolution call) should record
len(turn_log.events) beforehand and slice from there afterward (see
server/session.py).

Every event is a plain JSON-ready dict (no dataclass/Enum -- same
convention as stats.GameStats.captures/alliance_changes) with at least a
"kind" key; see each record_* method for that kind's other fields.
"""
from dataclasses import dataclass, field

from .combat import EventKind


@dataclass
class TurnLog:
    events: list = field(default_factory=list)

    def record_purchase(self, faction, orders, total_cost):
        self.events.append({
            'kind': 'purchase', 'faction': faction, 'total_cost': total_cost,
            'orders': [{'unit_type': o.unit_type, 'qty': o.qty, 'deploy_at': o.deploy_at} for o in orders],
        })

    def record_combat_move(self, faction, orders, unit_info=None):
        """`unit_info`: {unit_id: (unit_type, origin_territory_id)} as of
        BEFORE the orders executed (the engine captures it at confirm time),
        so a client can say "3x Armor moved Panama -> Costa Rica" without
        having to reconstruct where a unit was from an earlier snapshot.
        Each order gains 'unit_type' and 'from' when its unit is in it. Air
        units carried along as carrier ride-along have no order of their own
        and so don't appear here."""
        self.events.append({
            'kind': 'combat_move', 'faction': faction,
            'orders': [self._order_entry(o, unit_info, {'path': list(o.path)}) for o in orders],
        })

    def record_noncombat_move(self, faction, orders, unit_info=None):
        """Same `unit_info` contract as record_combat_move."""
        self.events.append({
            'kind': 'noncombat_move', 'faction': faction,
            'orders': [self._order_entry(o, unit_info, {'destination': o.destination}) for o in orders],
        })

    def record_return_to_base(self, faction, moves):
        """The automatic return-to-base step at the start of Non-Combat Move:
        `moves` is [(unit_id, unit_type, from_territory_id, to_territory_id), ...]
        for each air unit that actually moved (ones already home aren't
        listed). Same 'from'/'unit_type' order shape as a real move, with
        'to' as the destination."""
        self.events.append({
            'kind': 'return_to_base', 'faction': faction,
            'orders': [{'unit_id': uid, 'unit_type': ut, 'from': src, 'to': dst} for uid, ut, src, dst in moves],
        })

    def record_start_of_turn(self, faction, round_number, turn, turns_in_round):
        """The announcement that opens a faction's turn (a queue step of its own in
        server/stepper.py, not an engine phase): which round it is and which of
        the round's turns is starting."""
        self.events.append(self.start_of_turn_event(faction, round_number, turn, turns_in_round))

    @staticmethod
    def start_of_turn_event(faction, round_number, turn, turns_in_round):
        return {'kind': 'start_of_turn', 'faction': faction, 'round': round_number,
                'turn': turn, 'turns_in_round': turns_in_round}

    @staticmethod
    def _order_entry(order, unit_info, extra):
        entry = {'unit_id': order.unit_id, **extra}
        if unit_info and order.unit_id in unit_info:
            entry['unit_type'], entry['from'] = unit_info[order.unit_id]
        return entry

    def record_battle_events(self, territory_id, battle_type, events, attacker_units, defender_units):
        """`events`: the raw list[combat.BattleEvent] resolve_battle
        yielded for this one battle -- forwarded here almost verbatim
        (EventKind -> its .value string, dataclass -> plain dict), so a
        client can replay the exact same round-by-round, roll-by-roll
        sequence the engine itself just resolved, whether this battle
        belongs to a bot's whole-turn playback or a human's own Combat
        Resolution phase. `attacker_units`/`defender_units` supply the
        OWNER each unit_id belongs to (a BattleEvent only carries
        unit_type, not owner)."""
        owner_by_id = {u.unit_id: u.owner for u in attacker_units + defender_units}
        for e in events:
            entry = {
                'kind': 'battle_event', 'territory_id': territory_id, 'battle_type': battle_type,
                'event_kind': e.kind.value, 'round_number': e.round_number,
            }
            if e.kind == EventKind.UNIT_ROLL:
                entry.update(
                    side=e.side, unit_id=e.unit_id, unit_type=e.unit_type,
                    owner=owner_by_id.get(e.unit_id), die=e.die, roll=e.roll, hit=e.hit,
                    bypass_hit=e.bypass_hit, target_unit_id=e.target_unit_id,
                    target_owner=owner_by_id.get(e.target_unit_id), damage=e.damage,
                    target_hp_after=e.target_hp_after,
                )
            elif e.kind == EventKind.SIDE_START:
                entry.update(side=e.side)
            elif e.kind == EventKind.NO_TARGETS:
                entry.update(side=e.side, unit_id=e.unit_id, unit_type=e.unit_type, owner=owner_by_id.get(e.unit_id))
            elif e.kind == EventKind.PROMOTION:
                entry.update(
                    promoted_unit_id=e.promoted_unit_id, promoted_side=e.promoted_side, promotion_rank=e.promotion_rank,
                    owner=owner_by_id.get(e.promoted_unit_id),
                )
            elif e.kind == EventKind.UNIT_STATS:
                entry.update(stats_phase=e.stats_phase, units=e.unit_stats)
            elif e.kind == EventKind.BATTLE_END:
                entry.update(
                    outcome=e.outcome, end_reason=e.end_reason,
                    surviving_attacker_ids=e.surviving_attacker_ids, surviving_defender_ids=e.surviving_defender_ids,
                    eliminated_attacker_ids=e.eliminated_attacker_ids, eliminated_defender_ids=e.eliminated_defender_ids,
                )
            # AIR_SUPERIORITY_START / ROUND_START / ROUND_CASUALTIES: the
            # kind/round_number/territory_id/battle_type header above is
            # already the whole story worth narrating for these -- no
            # extra fields to add.
            self.events.append(entry)

        end = next((e for e in events if e.kind == EventKind.BATTLE_END), None)
        if end is not None:
            self._record_battle_summary(territory_id, battle_type, end, attacker_units, defender_units)

    def _record_battle_summary(self, territory_id, battle_type, end, attacker_units, defender_units):
        """One event per battle, after its roll-by-roll events: who fought on
        each side (unit type + owner) and who was eliminated, so a client can
        report participants and casualties without replaying the rolls.
        resolve_battle works on copies of the lists it's given, so
        `attacker_units`/`defender_units` still hold EVERY participant here,
        including the ones `end` lists as eliminated."""
        by_id = {u.unit_id: u for u in attacker_units + defender_units}

        def entry(unit_id):
            u = by_id[unit_id]
            return {'unit_id': unit_id, 'unit_type': u.unit_type, 'owner': u.owner}

        self.events.append({
            'kind': 'battle_summary', 'territory_id': territory_id, 'battle_type': battle_type,
            'outcome': end.outcome,
            'attackers': [entry(u.unit_id) for u in attacker_units],
            'defenders': [entry(u.unit_id) for u in defender_units],
            'eliminated_attackers': [entry(i) for i in end.eliminated_attacker_ids],
            'eliminated_defenders': [entry(i) for i in end.eliminated_defender_ids],
        })

    def record_capture(self, turn, faction, territory_id, previous_owner):
        self.events.append({
            'kind': 'territory_captured', 'turn': turn, 'faction': faction,
            'territory_id': territory_id, 'previous_owner': previous_owner,
        })

    def record_deploy(self, faction, territory_id, unit_type, qty):
        self.events.append({
            'kind': 'unit_deployed', 'faction': faction, 'territory_id': territory_id,
            'unit_type': unit_type, 'qty': qty,
        })

    def record_income(self, faction, amount):
        self.events.append({'kind': 'income_collected', 'faction': faction, 'amount': amount})

    def record_elimination(self, faction):
        self.events.append({'kind': 'faction_eliminated', 'faction': faction})

    def record_alliance_joined(self, turn, faction, target, tag, new_alliance):
        self.events.append({
            'kind': 'alliance_joined', 'turn': turn, 'faction': faction, 'target': target,
            'tag': tag, 'new_alliance': new_alliance,
        })

    def record_alliance_declined(self, turn, faction, target):
        self.events.append({'kind': 'alliance_declined', 'turn': turn, 'faction': faction, 'target': target})

    def record_alliance_withdrawal(self, turn, faction, tag, former_members):
        self.events.append({
            'kind': 'alliance_withdrawal', 'turn': turn, 'faction': faction,
            'tag': tag, 'former_members': sorted(former_members),
        })
