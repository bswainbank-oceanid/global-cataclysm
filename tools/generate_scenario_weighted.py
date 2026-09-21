"""
Generate the canonical starting-setup scenario (data/scenarios/starting_setup_125ipc.json):
125 MPC of units per faction, bought like this --

  1. One Infantry in every land territory the faction owns.
  2. The rest of the budget on units drawn by the faction's Unit Weights (the same odds the strategy
     bots buy by: data/bot_settings.json 'unit_weights', built from reference/GC Bot Settings.ods).
     Each purchase is a weighted draw of a unit type, then a placement: land and air units at a
     territory with room (weighted by the room left, so bigger spaces and Strategic Centers get
     more), naval units at one of the faction's coastal "naval territories" (deployed to its own sea
     zone, no zone shared between factions), an Aircraft Carrier always with an escorting Fighter
     or Bomber (drawn by the weights too) bought with it.
  3. Draws repeat (a fixed seed per faction, so the file is reproducible) until the faction has
     bought at least MIN_TYPES of the 8 unit types and spent exactly the budget: the leftover is
     closed with Infantry, and an attempt that cannot is thrown away.
  4. Three promotions: the three purchased unit types with the highest weights, one unit each, at the
     lowest-id territory that has that type.

Every setup rule in data/rules.json still holds (validate_setup.py checks them): the stacking cap
(value + 3, +2 at a Strategic Center), coastal-only naval purchases, no shared sea zones, a carrier
escort, a land unit at every foreign-bordering territory (the Infantry everywhere cover that).

Run from the repo root, after derived/faction_territory_profile.json has been (re)built:
    python3 tools/generate_scenario_weighted.py
Then tools/validate_setup.py, and tools/build_setup_tab.py for the spreadsheet tab.
"""
import json
import random
import sys

FACTION_ORDER = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']
LAND_TYPES = ['Infantry', 'Mechanized Infantry', 'Armor']
AIR_TYPES = ['Fighter', 'Bomber']
NAVAL_TYPES = ['Aircraft Carrier', 'Submarine', 'Cruiser']
UNIT_ORDER = LAND_TYPES + AIR_TYPES + NAVAL_TYPES

MIN_TYPES = 7
NAVAL_SPREAD_TARGET = 3
SEED = 1972
MAX_ATTEMPTS = 20000
ALWAYS_EXCLUDED_NAVAL_ZONE_IDS = {43}  # the Caspian Sea never hosts a naval deployment
OUT_PATH = 'data/scenarios/starting_setup_125ipc.json'


def naval_spread(profile, all_sea_spaces, cap_of):
    """Per faction, up to NAVAL_SPREAD_TARGET coastal territories (Strategic Centers, then biggest cap first)
    that host its naval units, each with its own sea zone, collision-free across the six factions (a zone the
    territory does not border is used only when a faction runs out of free neighbours: an 'override')."""
    claimed = set(ALWAYS_EXCLUDED_NAVAL_ZONE_IDS)
    out = {}
    for fac in sorted(FACTION_ORDER, key=lambda f: len([r for r in profile[f] if r['coastal']])):
        candidates = [r for r in profile[fac] if r['coastal']]
        candidates.sort(key=lambda r: (not r['sc'], -cap_of(r), -r['value'], r['id']))
        chosen = []
        for c in candidates:
            if len(chosen) >= NAVAL_SPREAD_TARGET:
                break
            if c['sea_zone'] in claimed:
                continue
            claimed.add(c['sea_zone'])
            chosen.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'zone': c['sea_zone_name'], 'override': False})
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
            chosen.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'zone': free[0]['name'], 'override': True})
            used.add(c['id'])
        out[fac] = chosen
    return out


class Attempt:
    """One faction's purchases, built up and thrown away if they cannot reach the rules."""

    def __init__(self, fac, rows, navals, units, weights, budget, rng):
        self.fac, self.rng, self.budget = fac, rng, budget
        self.units, self.weights = units, weights
        self.rows = {r['id']: r for r in rows}
        self.navals = navals
        self.purchases = {}   # tid -> {unit: qty}
        self.used = {}        # tid -> units bought there (all count against the cap)
        self.spend = 0
        self.escorts = []

    def cost(self, unit, tid):
        info = self.units[unit]
        return info['sc_cost'] if self.rows[tid]['sc'] else info['cost']

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
        return left >= 0 and left != 1  # 1 MPC can never be spent (the cheapest unit is 2)

    def place(self, unit, room_needed=1):
        """Where `unit` could be bought now (a list of territory ids), by room."""
        pool = [n['id'] for n in self.navals] if unit in NAVAL_TYPES else list(self.rows)
        return [t for t in pool if self.room(t) >= room_needed and self._ok_remainder(self.cost(unit, t))]

    def draw_type(self, allowed):
        weights = [self.weights.get(u, 0) for u in allowed]
        if not any(weights):
            return None
        return self.rng.choices(allowed, weights=weights)[0]

    def buy_one(self):
        """One weighted purchase (a carrier comes with its escort); False when nothing more can be bought."""
        allowed = list(UNIT_ORDER)
        while allowed:
            unit = self.draw_type(allowed)
            if unit is None:
                return False
            if unit == 'Aircraft Carrier':
                escort = self.draw_type(AIR_TYPES) or 'Fighter'
                spots = [t for t in self.place(unit, room_needed=2)
                         if self._ok_remainder(self.cost(unit, t) + self.cost(escort, t))]
                if spots:
                    tid = self.rng.choices(spots, weights=[self.room(t) for t in spots])[0]
                    self.add(unit, tid)
                    self.add(escort, tid)
                    self.escorts.append({'carrier_tid': tid, 'aircraft_tid': tid, 'unit': escort, 'qty': 1})
                    return True
            else:
                spots = self.place(unit)
                if spots:
                    tid = self.rng.choices(spots, weights=[self.room(t) for t in spots])[0]
                    self.add(unit, tid)
                    return True
            allowed.remove(unit)
        return False

    def fill_with_infantry(self):
        """Spend what is left, exactly, on Infantry (3 MPC, 2 at a Strategic Center)."""
        while self.spend < self.budget:
            spots = self.place('Infantry')
            if not spots:
                return False
            self.add('Infantry', self.rng.choice(spots))
        return self.spend == self.budget


def main():
    rules = json.load(open('data/rules.json', encoding='utf-8'))['setup']
    budget = rules['starting_unit_budget_mpc']
    promotions_count = rules['promotions_per_faction_at_setup']
    units = {n: i for n, i in json.load(open('data/units.json', encoding='utf-8'))['units'].items() if i['purchasable']}
    weights = json.load(open('data/bot_settings.json', encoding='utf-8'))['unit_weights']
    profile = json.load(open('derived/faction_territory_profile.json'))
    spaces = json.load(open('data/territories.json', encoding='utf-8'))['spaces']
    all_sea = sorted((s for s in spaces if s['type'] == 'sea'), key=lambda s: s['id'])
    navals = naval_spread(profile, all_sea, lambda r: r['cap'])

    purchases, promotions, escorts, overrides = {}, {}, {}, {}
    for fac in FACTION_ORDER:
        rows = [r for r in profile[fac] if r['cap'] >= 1]
        result = None
        for attempt in range(MAX_ATTEMPTS):
            rng = random.Random(f'{SEED}-{fac}-{attempt}')
            a = Attempt(fac, rows, navals[fac], units, weights[fac], budget, rng)
            for r in rows:
                a.add('Infantry', r['id'])
            while a.buy_one():
                pass
            if a.fill_with_infantry() and len(a.types()) >= MIN_TYPES:
                result = a
                break
        if result is None:
            sys.exit(f'{fac}: no purchase list with {MIN_TYPES} unit types and exactly {budget} MPC in {MAX_ATTEMPTS} attempts')
        print(f'{fac}: attempt {attempt + 1}, {len(result.types())} types, {sum(result.used.values())} units')
        purchases[fac] = result.purchases
        escorts[fac] = result.escorts
        # promotions: the purchased types with the highest weights, one unit each at the lowest-id territory holding it
        first = {}
        for tid, bought in sorted(result.purchases.items()):
            for unit in bought:
                first.setdefault(unit, tid)
        best = sorted(first, key=lambda u: (-weights[fac].get(u, 0), UNIT_ORDER.index(u)))[:promotions_count]
        promotions[fac] = [{'territory_id': first[u], 'unit': u} for u in best]
        for nt in navals[fac]:
            if nt['override']:
                here = result.purchases.get(nt['id'], {})
                naval_here = {u: nt['zone'] for u in here if u in NAVAL_TYPES}
                if naval_here:
                    overrides.setdefault(fac, {})[str(nt['id'])] = naval_here

    scenario = {
        '_comment': (f'Starting-setup scenario: {budget} MPC of units per faction. One Infantry in every land territory, then units '
                     'bought by each faction\'s Unit Weights (data/bot_settings.json), at least '
                     f'{MIN_TYPES} unit types each, {promotions_count} promotions each. Generated by tools/generate_scenario_weighted.py.'),
        'budget_ipc': budget,
        'purchases': {
            fac: [{'territory_id': tid, 'units': [{'unit': u, 'qty': q} for u, q in sorted(bought.items())]}
                  for tid, bought in sorted(purchases[fac].items())]
            for fac in FACTION_ORDER},
        'promotions': promotions,
        'carrier_escorts': {fac: e for fac, e in escorts.items() if e},
        'naval_deploy_overrides': overrides,
    }
    json.dump(scenario, open(OUT_PATH, 'w'), indent=2)
    print('wrote', OUT_PATH)


if __name__ == '__main__':
    main()
