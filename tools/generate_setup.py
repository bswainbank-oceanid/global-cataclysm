"""
Generate a starting setup: the scenario's standard or defensive InitialSetup and its
UnitPromotions (docs/DATA_MODEL.md). For each faction, bought like this --

  1. One garrison unit (the cheapest purchasable land unit with the mustering ability:
     Infantry) in every land territory the faction owns that has room for one.
  2. The rest of the budget on units drawn by the faction's unit weights (the same odds the
     strategy bots buy by: the scenario's FactionWeightSet). Each purchase is a weighted draw
     of a unit type, then a placement: land and air units at a territory with room (weighted
     by the room left, so bigger spaces and Strategic Centers get more), naval units at one
     of the faction's coastal "naval territories" (deployed to its sea zone -- the faction
     assignment's sea_deployment_location_id -- no zone shared between factions), a carrier
     (carrier_air_wing ability) always with an escorting aircraft (drawn by the weights too)
     bought with it.
  3. Draws repeat (a fixed seed per faction, so the output is reproducible) until the faction
     has bought at least min_unit_types unit types and spent the budget (exactly, or within
     budget_tolerance): the leftover is closed with the garrison unit, and an attempt that
     cannot is thrown away.
  4. `promotions` promotions: the purchased unit types with the highest weights, one unit
     each, at the lowest-id territory that has that type.

The parameters are the setup's own `generation` block (budget, use_sc, cap_bonus,
min_unit_types, promotions, budget_tolerance): the stacking cap is value + cap_bonus, plus
the SC assignment's sc_bonus at a Strategic Center when use_sc (which also makes units
there cost their sc_cost). tools/validate_setup.py checks the result.

Run from the repo root, after derived/faction_territory_profile.json has been (re)built:
    python tools/generate_setup.py --setup standard           # writes the modules
    python tools/generate_setup.py --setup standard --check   # only says whether they would change
"""
import argparse
import copy
import random
import sys

import tool_data
from engine import abilities
from engine.repository import default_repository, dumps

NAVAL_SPREAD_TARGET = 3
SEED = 1972
MAX_ATTEMPTS = 20000


def naval_spread(profile, factions, all_sea_spaces, excluded, cap_of):
    """Per faction, up to NAVAL_SPREAD_TARGET coastal territories (Strategic Centers, then biggest cap first)
    that host its naval units, each with its own sea zone, collision-free across the factions (a zone the
    territory's sea deployment doesn't name is used only when a faction runs out of free ones: an 'override')."""
    claimed = set(excluded)
    out = {}
    for fac in sorted(factions, key=lambda f: len([r for r in profile[f] if r['coastal']])):
        candidates = [r for r in profile[fac] if r['coastal']]
        candidates.sort(key=lambda r: (not r['sc'], -cap_of(r), -r['value'], r['id']))
        chosen = []
        for c in candidates:
            if len(chosen) >= NAVAL_SPREAD_TARGET:
                break
            if c['sea_zone'] in claimed:
                continue
            claimed.add(c['sea_zone'])
            chosen.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'zone': c['sea_zone'], 'override': False})
        used = {x['id'] for x in chosen}
        for c in candidates:
            if len(chosen) >= NAVAL_SPREAD_TARGET or len(chosen) >= len(candidates):
                break
            if c['id'] in used:
                continue
            free = [z for z in all_sea_spaces if z['id'] not in claimed]
            if not free:
                break
            claimed.add(free[0]['id'])
            chosen.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'zone': free[0]['id'], 'override': True})
            used.add(c['id'])
        out[fac] = chosen
    return out


class Attempt:
    """One faction's purchases, built up and thrown away if they cannot reach the rules."""

    def __init__(self, fac, rows, navals, units, kinds, weights, budget, tolerance, use_sc, rng):
        self.fac, self.rng, self.budget, self.tolerance, self.use_sc = fac, rng, budget, tolerance, use_sc
        self.units, self.kinds, self.weights = units, kinds, weights
        self.rows = {r['id']: r for r in rows}
        self.navals = navals
        self.purchases = {}   # tid -> {unit: qty}
        self.used = {}        # tid -> units bought there (all count against the cap)
        self.spend = 0
        self.escorts = []     # (tid, air unit) bought with a carrier
        self.cheapest = min(min(i['cost'], i['sc_cost'] if use_sc else i['cost']) for i in units.values())

    def cost(self, unit, tid):
        info = self.units[unit]
        return info['sc_cost'] if self.use_sc and self.rows[tid]['sc'] else info['cost']

    def room(self, tid):
        return self.rows[tid]['cap'] - self.used.get(tid, 0)

    def add(self, unit, tid, qty=1):
        self.purchases.setdefault(tid, {})
        self.purchases[tid][unit] = self.purchases[tid].get(unit, 0) + qty
        self.used[tid] = self.used.get(tid, 0) + qty
        self.spend += self.cost(unit, tid) * qty

    def types(self):
        return {u for units in self.purchases.values() for u in units}

    def _ok_remainder(self, cost):
        left = self.budget - self.spend - cost
        return left >= 0 and (left == 0 or left >= self.cheapest)  # a smaller leftover could never be spent

    def place(self, unit, room_needed=1):
        """Where `unit` could be bought now (a list of territory ids), by room."""
        pool = [n['id'] for n in self.navals] if unit in self.kinds['Sea'] else list(self.rows)
        return [t for t in pool if self.room(t) >= room_needed and self._ok_remainder(self.cost(unit, t))]

    def draw_type(self, allowed):
        weights = [self.weights.get(u, 0) for u in allowed]
        if not any(weights):
            return None
        return self.rng.choices(allowed, weights=weights)[0]

    def buy_one(self):
        """One weighted purchase (a carrier comes with its escort); False when nothing more can be bought."""
        allowed = list(self.kinds['all'])
        while allowed:
            unit = self.draw_type(allowed)
            if unit is None:
                return False
            if unit in self.kinds['carriers']:
                escort = self.draw_type(self.kinds['Air']) or self.kinds['Air'][0]
                spots = [t for t in self.place(unit, room_needed=2)
                         if self._ok_remainder(self.cost(unit, t) + self.cost(escort, t))]
                if spots:
                    tid = self.rng.choices(spots, weights=[self.room(t) for t in spots])[0]
                    self.add(unit, tid)
                    self.add(escort, tid)
                    self.escorts.append((tid, escort))
                    return True
            else:
                spots = self.place(unit)
                if spots:
                    tid = self.rng.choices(spots, weights=[self.room(t) for t in spots])[0]
                    self.add(unit, tid)
                    return True
            allowed.remove(unit)
        return False

    def fill_with_garrison(self):
        """Spend what is left on the garrison unit (exactly, or to within the tolerance)."""
        garrison = self.kinds['garrison']
        while self.budget - self.spend > self.tolerance:
            spots = self.place(garrison)
            if not spots:
                break
            self.add(garrison, self.rng.choice(spots))
        return 0 <= self.budget - self.spend <= self.tolerance


def unit_kinds(units):
    """The purchasable unit types by category (in unit set order: land, air, sea), the carriers, and the
    garrison unit."""
    buyable = {n: i for n, i in units.items() if i['purchasable']}
    by_cat = {c: [n for n, i in buyable.items() if i['category'] == c] for c in ('Land', 'Air', 'Sea')}
    musterers = [n for n in by_cat['Land'] if abilities.has(units, n, abilities.MUSTERING)]
    garrison = min(musterers or by_cat['Land'], key=lambda n: (buyable[n]['cost'], by_cat['Land'].index(n)))
    return dict(by_cat, all=by_cat['Land'] + by_cat['Air'] + by_cat['Sea'], garrison=garrison,
                carriers=[n for n in by_cat['Sea'] if abilities.has(units, n, abilities.CARRIER_AIR_WING)])


def generate(setup, promotions_doc, profile, seed=SEED):
    """New (InitialSetup, UnitPromotions) documents, keeping the given ones' ids, names and references."""
    gen = setup['generation']
    budget, use_sc, cap_bonus = gen['budget'], gen['use_sc'], gen['cap_bonus']
    min_types, promotions_count, tolerance = gen['min_unit_types'], gen['promotions'], gen.get('budget_tolerance', 0)
    config = tool_data.config()
    sc_bonus = config.sc_bonus()
    units = tool_data.units()
    kinds = unit_kinds(units)
    buyable = {n: units[n] for n in kinds['all']}
    weights = tool_data.unit_weights()
    factions = list(tool_data.factions())
    terrs = config.territories()

    def cap_of(r):
        return r['value'] + cap_bonus + (sc_bonus if use_sc and r['sc'] else 0)

    profile = {fac: [dict(r, cap=cap_of(r)) for r in rows] for fac, rows in profile.items()}
    all_sea = sorted((t for t in terrs.values() if t['type'] == 'sea'), key=lambda s: s['id'])
    navals = naval_spread(profile, factions, all_sea, config.naval_deploy_excluded(), lambda r: r['cap'])

    placed, promoted = {}, {}
    for fac in factions:
        rows = [r for r in profile.get(fac, []) if r['cap'] >= 1]
        result = None
        for attempt in range(MAX_ATTEMPTS):
            rng = random.Random(f'{seed}-{fac}-{attempt}')
            a = Attempt(fac, rows, navals[fac], buyable, kinds, weights.get(fac, {}), budget, tolerance, use_sc, rng)
            for r in rows:
                a.add(kinds['garrison'], r['id'])
            while a.buy_one():
                pass
            if a.fill_with_garrison() and len(a.types()) >= min_types:
                result = a
                break
        if result is None:
            sys.exit(f'{fac}: no purchase list with {min_types} unit types and {budget} MPC in {MAX_ATTEMPTS} attempts')
        print(f'{fac}: attempt {attempt + 1}, {len(result.types())} types, {sum(result.used.values())} units, '
              f'{result.spend} MPC')
        zone_of = {n['id']: n['zone'] for n in navals[fac]}
        escorts_left = {}
        for tid, air in result.escorts:
            escorts_left[(tid, air)] = escorts_left.get((tid, air), 0) + 1
        rows_out = []  # (unit type, location, bought at), in the order the units are placed
        for tid, bought in sorted(result.purchases.items()):
            for unit_type, qty in sorted(bought.items()):
                for _ in range(qty):
                    dest = tid
                    if units[unit_type]['category'] == 'Sea':
                        dest = zone_of[tid]
                    elif escorts_left.get((tid, unit_type), 0) > 0:
                        escorts_left[(tid, unit_type)] -= 1
                        dest = zone_of[tid]
                    rows_out.append((unit_type, dest, tid))
        placed[fac] = rows_out
        # promotions: the purchased types with the highest weights, one unit each at the lowest-id territory holding it
        first = {}
        for i, (unit_type, _, tid) in enumerate(rows_out):
            if unit_type not in first or tid < rows_out[first[unit_type]][2]:
                first[unit_type] = i
        best = sorted(first, key=lambda u: (-weights.get(fac, {}).get(u, 0), kinds['all'].index(u)))[:promotions_count]
        promoted[fac] = [first[u] for u in best]

    new_setup = {k: v for k, v in setup.items() if k != 'locations'}
    by_location = {}
    ids = {}
    for fac in factions:
        for i, (unit_type, dest, bought) in enumerate(placed[fac]):
            uid = f'{fac}-{i + 1:03d}'
            ids[(fac, i)] = uid
            entry = {'id': uid, 'unit_type_id': unit_type, 'faction_id': fac}
            if bought != dest:
                entry['purchased_at'] = bought
            by_location.setdefault(dest, []).append(entry)
    new_setup['locations'] = [{'location_id': tid, 'units': by_location[tid]} for tid in terrs if tid in by_location]
    new_promotions = copy.deepcopy(promotions_doc)
    new_promotions['units'] = [{'unit_id': ids[(fac, i)], 'num_promotions': 1}
                               for fac in factions for i in promoted.get(fac, [])]
    return new_setup, new_promotions


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
    parser.add_argument('--setup', choices=['standard', 'defensive'], default='standard')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--check', action='store_true', help='report whether the stored setup would change; write nothing')
    args = parser.parse_args()
    tool_data.use_scenario(args.scenario)

    setup, promotions = tool_data.config().initial_setup(args.setup)
    with open(tool_data.root_path('derived/faction_territory_profile.json'), encoding='utf-8') as f:
        profile = json.load(f)
    new_setup, new_promotions = generate(setup, promotions, profile, args.seed)
    changed = [d['id'] for d, old in ((new_setup, setup), (new_promotions, promotions)) if dumps(d) != dumps(old)]
    if args.check:
        print('would change: ' + ', '.join(changed) if changed else 'the stored setup matches the generator')
        sys.exit(1 if changed else 0)
    repo = default_repository()
    for d in (new_setup, new_promotions):
        repo.save(d)
        print('wrote', repo.path(d['module_type'], d['id']))


if __name__ == '__main__':
    main()
