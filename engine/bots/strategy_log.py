"""
The Strategy Log (reference/GC Strategy Log.odt): what a strategy bot decided each turn and why, for
evaluating bot strategy. Built from the planner's own record (bots/planner.py: which objective and
target each unit and purchase serves, every target each objective looked at and why it was or was not
pursued); nothing here changes the game or the bot's choices.

A StrategyLogger follows one bot faction through its turns (server/stepper.py drives it) and makes
turn_log-shaped events, every territory an id and every objective {no, id, name}:

    strategy_turn_start {faction, style, stats, change}
        stats: {territory_mpc, unit_value, unit_count, scs}; change: since the start of the faction's
        previous turn (null on its first)
    strategy_phase {faction, phase, choices, rejected}
        phase PURCHASE: choices [{unit_type, qty, at, objective, target}]
        phase COMBAT_MOVE / NONCOMBAT_MOVE: choices [{units: [{unit_type, count}], from, to, objective,
        target}] -- `to` equal to `from` with "hold": true for units told to stay put (Non-Combat Move)
        rejected: what the plan wanted but the engine would not take, in the same shapes
    strategy_turn_end {faction, style, stats, change, review}
        change: during this turn; review: {purchase, combat, noncombat: those phases' choices;
        rejected: [{phase, ...}]; idle_units: [{unit_type, count, at}] (units with no orders at all);
        no_resources: [{objective, reasons: [{target, reason}], more}] (objectives given nothing, and why)}

The objective numbers are the planning order of the pass that made the choice: purchases and combat
moves come from the plan made at Purchase, non-combat moves from the one made again after combat.
Secondary objectives are numbered in that turn's draw order.
"""
from ..economy import compute_income

REASONS_SHOWN = 4  # an objective's why-nots listed in full; the rest are counted


def faction_stats(engine, faction):
    gs, data = engine.game_state, engine.data
    units = [u for t in gs.territories.values() for u in t.units if u.owner == faction]
    defs = data.units()
    terrs = data.territories()
    scs = sum(1 for tid, t in gs.territories.items()
              if t.owner == faction and terrs[tid]['type'] == 'land' and gs.is_strategic_center(tid, terrs[tid]))
    return {'territory_mpc': compute_income(faction, gs, data),
            'unit_value': sum(defs.get(u.unit_type, {}).get('cost') or 0 for u in units),
            'unit_count': len(units), 'scs': scs}


def _change(now, before):
    return None if before is None else {k: now[k] - before[k] for k in now}


def _objectives(plan):
    return {o['id']: o for o in plan.objectives} if plan is not None else {}


def _objective(plan, objective_id):
    o = _objectives(plan).get(objective_id)
    return dict(o) if o else {'no': None, 'id': objective_id, 'name': objective_id.replace('_', ' ').capitalize()}


def _grouped_units(units):
    counts = {}
    for unit_type in units:
        counts[unit_type] = counts.get(unit_type, 0) + 1
    return [{'unit_type': t, 'count': n} for t, n in counts.items()]


class StrategyLogger:
    def __init__(self, engine, bot):
        self.engine = engine
        self.bot = bot
        self.faction = bot.faction
        self._last_start = None   # stats at the start of the previous turn
        self._start = None        # ...and of this one
        self._review = {'purchase': [], 'combat': [], 'noncombat': [], 'rejected': [], 'idle_units': []}
        self._full_plan = None    # the plan the Purchase and Combat Move choices came from
        self._staged = {}         # phase -> (staged orders, {unit_id: (unit_type, origin)}) captured before confirm

    # ---- the turn ------------------------------------------------------------------------------

    def turn_start(self):
        stats = faction_stats(self.engine, self.faction)
        self._last_start, self._start = self._start, stats
        self._review = {'purchase': [], 'combat': [], 'noncombat': [], 'rejected': [], 'idle_units': []}
        self._full_plan = None
        return {'kind': 'strategy_turn_start', 'faction': self.faction, 'style': self.bot.style,
                'stats': stats, 'change': _change(stats, self._last_start)}

    def before_commit(self, phase):
        """Captures what is staged for `phase` (and where each unit is) before the engine executes it."""
        e, f = self.engine, self.faction
        if phase == 'PURCHASE':
            self._staged[phase] = (list(e._staged_purchases.get(f, [])), {})
        elif phase in ('COMBAT_MOVE', 'NONCOMBAT_MOVE'):
            orders = list((e._staged_combat_moves if phase == 'COMBAT_MOVE' else e._staged_noncombat_moves).get(f, []))
            where = {u.unit_id: (u.unit_type, tid) for tid, t in e.game_state.territories.items() for u in t.units
                     if u.owner == f}
            self._staged[phase] = (orders, where)

    def phase(self, phase):
        """The strategy_phase event for `phase`, once it has executed (None for a phase with no choices)."""
        staged, where = self._staged.pop(phase, (None, None))
        if staged is None:
            return None
        if phase == 'PURCHASE':
            plan = self._full_plan = self.bot._plan
            choices, rejected = self._purchases(plan, staged)
            key = 'purchase'
        elif phase == 'COMBAT_MOVE':
            plan = self._full_plan or self.bot._plan
            choices, rejected = self._moves(plan, plan.combat if plan else [], staged, where, 'combat')
            key = 'combat'
        else:
            plan = self.bot.last_plan
            choices, rejected = self._moves(plan, plan.noncombat if plan else [], staged, where, 'noncombat')
            choices += self._holds(plan, where)
            self._review['idle_units'] = self._idle(plan)
            key = 'noncombat'
        self._review[key] = choices
        self._review['rejected'] += [dict(r, phase=phase) for r in rejected]
        return {'kind': 'strategy_phase', 'faction': self.faction, 'phase': phase, 'choices': choices,
                'rejected': rejected}

    def turn_end(self):
        stats = faction_stats(self.engine, self.faction)
        review = dict(self._review)
        review['no_resources'] = self._no_resources()
        return {'kind': 'strategy_turn_end', 'faction': self.faction, 'style': self.bot.style,
                'stats': stats, 'change': _change(stats, self._start), 'review': review}

    # ---- choices -------------------------------------------------------------------------------

    def _purchases(self, plan, staged):
        if plan is None:
            return [], []
        planned = {}
        for o in plan.purchases:
            planned[(o.unit_type, o.deploy_at)] = planned.get((o.unit_type, o.deploy_at), 0) + o.qty
        bought = {}
        for o in staged:
            bought[(o.unit_type, o.deploy_at)] = bought.get((o.unit_type, o.deploy_at), 0) + o.qty
        left = dict(bought)
        groups = {}
        for unit_type, at, objective_id, target in plan.purchase_why:
            if left.get((unit_type, at), 0) <= 0:
                continue  # (rejected: listed below)
            left[(unit_type, at)] -= 1
            key = (unit_type, at, objective_id, target)
            groups[key] = groups.get(key, 0) + 1
        choices = [{'unit_type': t, 'qty': n, 'at': at, 'objective': _objective(plan, oid), 'target': target}
                   for (t, at, oid, target), n in groups.items()]
        choices.sort(key=lambda c: (c['objective']['no'] or 99, c['at'], c['unit_type']))
        rejected = [{'unit_type': t, 'qty': q - bought.get((t, at), 0), 'at': at}
                    for (t, at), q in sorted(planned.items()) if q > bought.get((t, at), 0)]
        return choices, rejected

    def _moves(self, plan, planned, staged, where, kind):
        def end(order):
            return order.path[-1] if kind == 'combat' else order.destination

        groups = {}
        for o in staged:
            unit_type, origin = where.get(o.unit_id, ('?', None))
            oid, target = (plan.unit_why.get(o.unit_id) if plan else None) or ('unplanned', None)
            key = (origin, end(o), oid, target)
            groups.setdefault(key, []).append(unit_type)
        choices = [{'units': _grouped_units(units), 'from': origin, 'to': to, 'objective': _objective(plan, oid),
                    'target': target} for (origin, to, oid, target), units in groups.items()]
        choices.sort(key=lambda c: (c['objective']['no'] or 99, c['from'] or 0, c['to']))
        done = {o.unit_id for o in staged}
        rejected = []
        for o in planned:
            if o.unit_id in done:
                continue
            unit_type, origin = where.get(o.unit_id, ('?', None))
            oid, target = plan.unit_why.get(o.unit_id) or ('unplanned', None)
            rejected.append({'units': [{'unit_type': unit_type, 'count': 1}], 'from': origin, 'to': end(o),
                             'objective': _objective(plan, oid), 'target': target})
        return choices, rejected

    def _holds(self, plan, where):
        """Units the non-combat plan told to stay where they are (garrisons), by objective."""
        if plan is None:
            return []
        moving = {o.unit_id for o in plan.noncombat}
        groups = {}
        for uid in sorted(plan.stay):
            if uid not in where or uid in moving:
                continue
            unit_type, at = where[uid]
            oid, target = plan.unit_why.get(uid) or ('unplanned', None)
            groups.setdefault((at, oid, target), []).append(unit_type)
        return [{'units': _grouped_units(units), 'from': at, 'to': at, 'hold': True, 'objective': _objective(plan, oid),
                 'target': target} for (at, oid, target), units in sorted(groups.items(), key=lambda kv: (
                     _objective(plan, kv[0][1])['no'] or 99, kv[0][0]))]

    def _idle(self, nc_plan):
        """The faction's units on the board that got no orders this turn: no combat move, no non-combat move,
        and not told to stay put by either plan."""
        told = set(nc_plan.stay if nc_plan else ()) | set(self._full_plan.stay if self._full_plan else ())
        groups = {}
        for tid, t in self.engine.game_state.territories.items():
            for u in t.units:
                if u.owner != self.faction or u.has_moved_combat or u.has_moved_noncombat or u.unit_id in told:
                    continue
                groups[(u.unit_type, tid)] = groups.get((u.unit_type, tid), 0) + 1
        return [{'unit_type': t, 'count': n, 'at': tid} for (t, tid), n in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]))]

    # ---- objectives given nothing ----------------------------------------------------------------

    def _no_resources(self):
        full, nc = self._full_plan or self.bot._plan, self.bot.last_plan
        used = set()
        if full is not None:
            used |= {oid for _, _, oid, _ in full.purchase_why}
            combat_units = {o.unit_id for o in full.combat}
            used |= {why[0] for uid, why in full.unit_why.items() if uid in combat_units or uid in full.stay}
        if nc is not None and nc is not full:
            nc_units = {o.unit_id for o in nc.noncombat} | set(nc.stay)
            used |= {why[0] for uid, why in nc.unit_why.items() if uid in nc_units}
        order = []
        for plan in (full, nc):
            for o in (plan.objectives if plan else []):
                if o['id'] not in [x['id'] for x in order]:
                    order.append(o)
        out = []
        for o in order:
            if o['id'] in used:
                continue
            reasons, seen = [], set()
            for plan in (full, nc):
                for a in (plan.attempts if plan else []):
                    key = a['target'] if a['target'] is not None else a['reason']
                    if a['objective'] != o['id'] or key in seen:
                        continue  # (one reason per target: the plan made at Purchase first)
                    seen.add(key)
                    reasons.append({'target': a['target'], 'reason': a['reason']})
            if not reasons:
                reasons = [{'target': None, 'reason': 'no targets'}]
            out.append({'objective': dict(o), 'reasons': reasons[:REASONS_SHOWN], 'more': max(0, len(reasons) - REASONS_SHOWN)})
        for plan in (full,):
            for s in (plan.skipped if plan else []):
                out.append({'objective': {'no': None, 'id': s['id'], 'name': s['name']},
                            'reasons': [{'target': None, 'reason': 'not planned: ' + s['reason']}], 'more': 0})
        return out
