"""
The New Scenario generator: from a base scenario (its map, territory values, unit set,
factions, rules and bot weights) and a set of options, deal out a fresh game -- which
factions hold which territory, where the Strategic Centers are, and every starting unit --
for the factions actually playing. See reference/GC72 New Scenario.odt and
docs/DATA_MODEL.md (ScenarioGenerator).

generate(base, players, options, rng) returns (repository, scenario_id, report): an
OverlayRepository holding the generated Scenario and its generated modules in memory on
top of the base ones (nothing is written), the new Scenario's id, and notes on anything the
generator had to relax. Everything is drawn from `rng`, so the same seed deals the same game.

The deal:
  1. Territory. Each playing faction may hold up to faction_value_pool / players value
     (rounded down). In a random order fixed for the deal, the factions take turns drawing one
     unallocated land territory each -- weighted value + 1, + faction_weight if the territory is
     that faction's on the base map -- skipping any that would take it over its limit, until
     every faction is full or nothing is left. The Neutral pool then draws the same way (value
     + 1) up to neutral_value (everything left, at its maximum); whatever remains is
     Noncombatant.
  2. Strategic Centers. Round by round, each faction draws one of its territories (value + 1)
     at least sc_min_distance steps (the adjacency graph, land and sea) from every Strategic
     Center placed so far, its last one also at least sc_final_min_distance from its own others;
     then neutral_scs Neutral ones, sc_min_distance from all. A deal that cannot place them all
     is retried; if it keeps failing, the distances are relaxed one step at a time (reported).
  3. Units, per faction, from initial_mpc: an Infantry (the garrison unit) in every territory
     (when infantry_in_every_territory), extra_non_sc_units more at territories that are not
     Strategic Centers (weighted value + 1), then the rest at randomly chosen Strategic Centers
     until nothing affordable fits; each unit type drawn by the faction's unit weights. The
     starting setup rules hold: a territory's stacking cap, naval units at a coastal territory's
     own sea zone (no zone shared between factions, none in an excluded zone), a carrier bought
     with an escorting aircraft, at least the base setup's minimum of unit types (retried; fewer
     accepted if it can't be reached), promotions for the highest-weighted types bought. The
     unspent budget carries over into the faction's first turn.
  4. Neutral units, from neutral_budget: an Infantry in every Neutral territory (when
     neutral_infantry_in_every_territory), then land and air units drawn by the Neutral unit
     weights, placed weighted value + 1 (+ the SC bonus at a Strategic Center), until nothing
     fits. No promotions.
"""
import copy
from collections import deque

from . import abilities
from .repository import OverlayRepository

# The options, in the order the launcher shows them: key -> (label, kind, min, max). A max of None is
# worked out from the base scenario (the map's total land value).
OPTIONS = {
    'faction_value_pool': ('Faction territory value (total, split among the players)', 'int', 0, None),
    'neutral_value': ('Neutral territory value (the rest is Noncombatant)', 'int', 0, None),
    'faction_weight': ('Territory faction weight (a faction\'s own base-map territory)', 'int', 0, 100),
    'scs_per_faction': ('Strategic Centers per faction', 'int', 0, 10),
    'sc_bonus': ('Strategic Center bonus', 'int', 0, 10),
    'sc_min_distance': ('Strategic Center minimum distance', 'int', 0, 12),
    'sc_final_min_distance': ('Final Strategic Center minimum distance (from your own)', 'int', 0, 12),
    'neutral_scs': ('Neutral Strategic Centers', 'int', 0, 20),
    'initial_mpc': ('Initial faction MPC', 'int', 0, 1000),
    'infantry_in_every_territory': ('Faction Infantry in every territory', 'bool', None, None),
    'extra_non_sc_units': ('Additional units outside Strategic Centers', 'int', 0, 100),
    'neutral_budget': ('Neutral MPC', 'int', 0, 1000),
    'neutral_infantry_in_every_territory': ('Neutral Infantry in every territory', 'bool', None, None),
    'min_scs_to_avoid_surrender': ('Strategic Centers needed to avoid surrender', 'int', 0, 10),
    'surrender_income_multiplier': ('Income multiple that forces surrender', 'num', 1, 10),
}

SC_ATTEMPTS = 200       # deals of the Strategic Centers tried before relaxing the distances
UNIT_ATTEMPTS = 40      # purchase lists tried per faction for the unit-type minimum
NAVAL_TERRITORIES = 3   # coastal territories per faction that host its naval units


def land_value_total(base):
    return sum(t['value'] for t in base.territories().values() if t['type'] == 'land')


def option_specs(base, defaults):
    """[{key, label, kind, min, max, default}] for the launcher, maxima filled in from the base scenario."""
    total = land_value_total(base)
    out = []
    for key, (label, kind, lo, hi) in OPTIONS.items():
        default = defaults.get(key)
        if key == 'neutral_value' and default is None:
            default = total
        out.append({'key': key, 'label': label, 'kind': kind, 'min': lo, 'max': total if hi is None else hi,
                    'default': default})
    return out


def check_options(base, options):
    """Problems with `options` (a complete set: defaults already filled in), as readable strings."""
    problems = []
    for spec in option_specs(base, {}):
        v = options.get(spec['key'])
        if spec['kind'] == 'bool':
            if not isinstance(v, bool):
                problems.append(f'{spec["label"]}: must be true or false')
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (spec['kind'] == 'int' and v != int(v)):
            problems.append(f'{spec["label"]}: must be a {"whole " if spec["kind"] == "int" else ""}number')
        elif not spec['min'] <= v <= spec['max']:
            problems.append(f'{spec["label"]}: must be between {spec["min"]} and {spec["max"]}')
    return problems


def resolve_options(base, defaults, overrides):
    """The generator's defaults with the launcher's choices on top (neutral_value None: all unclaimed)."""
    out = dict(defaults)
    out.update({k: v for k, v in (overrides or {}).items() if k in OPTIONS})
    if out.get('neutral_value') is None:
        out['neutral_value'] = land_value_total(base)
    for spec in option_specs(base, {}):
        v = out.get(spec['key'])
        if spec['kind'] == 'int' and isinstance(v, float) and v == int(v):
            out[spec['key']] = int(v)
    return out


# ---- the deal ------------------------------------------------------------------------------------

def _weighted(rng, items, weight):
    weights = [weight(i) for i in items]
    total = sum(weights)
    if not items or total <= 0:
        return None
    r = rng.random() * total
    acc = 0.0
    for item, w in zip(items, weights):
        acc += w
        if r < acc:
            return item
    return items[-1]


def _distances(adjacency):
    """{a: {b: steps}} over the whole adjacency graph (land and sea)."""
    out = {}
    for start in adjacency:
        seen = {start: 0}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for n in adjacency.get(cur, []):
                if n not in seen:
                    seen[n] = seen[cur] + 1
                    queue.append(n)
        out[start] = seen
    return out


def allocate_territory(base, players, options, rng):
    """{land id: owner} -- a player faction, the Neutral faction's id, or the Noncombatant's."""
    terrs = base.territories()
    land = sorted(t for t, info in terrs.items() if info['type'] == 'land')
    limit = options['faction_value_pool'] // max(1, len(players))
    held = {f: 0 for f in players}
    owner = {}
    order = list(players)
    rng.shuffle(order)
    remaining = list(land)
    full = set()
    while remaining and len(full) < len(order):
        for f in order:
            if f in full:
                continue
            room = limit - held[f]
            fits = [t for t in remaining if terrs[t]['value'] <= room] if room > 0 else []
            if not fits:
                full.add(f)
                continue
            pick = _weighted(rng, fits, lambda t: terrs[t]['value'] + 1
                             + (options['faction_weight'] if terrs[t].get('faction') == f else 0))
            owner[pick] = f
            held[f] += terrs[pick]['value']
            remaining.remove(pick)
    neutral_id = base.neutral_faction()['id']
    noncombatant_id = base.noncombatant_faction()['id']
    unclaimed = sum(terrs[t]['value'] for t in remaining)
    if options['neutral_value'] >= unclaimed:
        for t in remaining:
            owner[t] = neutral_id
        remaining = []
    else:
        room = options['neutral_value']
        while remaining and room > 0:
            fits = [t for t in remaining if terrs[t]['value'] <= room]
            if not fits:
                break
            pick = _weighted(rng, fits, lambda t: terrs[t]['value'] + 1)
            owner[pick] = neutral_id
            room -= terrs[pick]['value']
            remaining.remove(pick)
    for t in remaining:
        owner[t] = noncombatant_id
    return owner


def place_strategic_centers(base, players, owner, options, rng):
    """(sorted SC ids, notes): the Strategic Centers, relaxing the distances when they can't all be placed."""
    terrs = base.territories()
    dist = _distances(base.adjacency())
    neutral_id = base.neutral_faction()['id']
    min_d, final_d = options['sc_min_distance'], options['sc_final_min_distance']
    notes = []
    while True:
        for _ in range(SC_ATTEMPTS):
            placed = _try_scs(terrs, dist, players, owner, neutral_id, options, min_d, final_d, rng)
            if placed is not None:
                if (min_d, final_d) != (options['sc_min_distance'], options['sc_final_min_distance']):
                    notes.append(f'Strategic Center distances relaxed to {min_d} (final {final_d}) to fit them all')
                return sorted(placed), notes
        if min_d == 0 and final_d == 0:
            notes.append('not every Strategic Center could be placed')
            return [], notes
        min_d, final_d = max(0, min_d - 1), max(0, final_d - 1)


def _try_scs(terrs, dist, players, owner, neutral_id, options, min_d, final_d, rng):
    per = options['scs_per_faction']
    placed, own = [], {f: [] for f in players}
    holdings = {f: [t for t, o in owner.items() if o == f] for f in players}
    want = {f: min(per, len(holdings[f])) for f in players}
    for rnd in range(per):
        for f in players:
            if rnd >= want[f]:
                continue
            final = rnd == want[f] - 1 and want[f] > 1
            cands = [t for t in holdings[f] if t not in placed
                     and all(dist[t].get(s, 999) >= min_d for s in placed)
                     and (not final or all(dist[t].get(s, 999) >= final_d for s in own[f]))]
            pick = _weighted(rng, cands, lambda t: terrs[t]['value'] + 1)
            if pick is None:
                return None
            placed.append(pick)
            own[f].append(pick)
    neutral_land = [t for t, o in owner.items() if o == neutral_id]
    for _ in range(min(options['neutral_scs'], len(neutral_land))):
        cands = [t for t in neutral_land if t not in placed and all(dist[t].get(s, 999) >= min_d for s in placed)]
        pick = _weighted(rng, cands, lambda t: terrs[t]['value'] + 1)
        if pick is None:
            return None
        placed.append(pick)
    return placed


class _Buyer:
    """One faction's starting purchases, within its budget and the setup rules."""

    def __init__(self, base, faction, holdings, scs, navals, options, rng, weights, cap_bonus):
        self.terrs, self.units = base.territories(), base.units()
        self.faction, self.rng, self.weights = faction, rng, weights
        self.holdings, self.scs, self.navals = holdings, set(scs), navals  # navals: {land id: its sea zone}
        self.sc_bonus, self.cap_bonus = options['sc_bonus'], cap_bonus
        self.budget = 0
        self.bought = []        # (unit type, land id bought at, is escort)
        self.used = {}

    def cap(self, tid):
        return self.terrs[tid]['value'] + self.cap_bonus + (self.sc_bonus if tid in self.scs else 0)

    def room(self, tid):
        return self.cap(tid) - self.used.get(tid, 0)

    def cost(self, unit, tid):
        info = self.units[unit]
        return info['sc_cost'] if tid in self.scs else info['cost']

    def add(self, unit, tid, escort=False):
        self.bought.append((unit, tid, escort))
        self.used[tid] = self.used.get(tid, 0) + 1
        self.budget -= self.cost(unit, tid)

    def spots(self, unit, pool):
        """Where `unit` could be bought now among `pool` (land ids), with room and money for it (and for
        a carrier, its escort)."""
        cat = self.units[unit]['category']
        carrier = abilities.has(self.units, unit, abilities.CARRIER_AIR_WING)
        out = []
        for t in pool:
            if cat == 'Sea' and t not in self.navals:
                continue
            need = self.cost(unit, t) + (self.cheapest_air(t) if carrier else 0)
            if self.room(t) >= (2 if carrier else 1) and need <= self.budget:
                out.append(t)
        return out

    def air_types(self):
        return [u for u, i in self.units.items() if i['purchasable'] and i['category'] == 'Air']

    def cheapest_air(self, tid):
        return min((self.cost(u, tid) for u in self.air_types()), default=10 ** 9)

    def buy(self, unit, tid):
        self.add(unit, tid)
        if abilities.has(self.units, unit, abilities.CARRIER_AIR_WING):
            affordable = [u for u in self.air_types() if self.cost(u, tid) <= self.budget]
            escort = _weighted(self.rng, affordable, lambda u: self.weights.get(u, 0) or 0.0001)
            self.add(escort, tid, escort=True)

    def draw(self, pool, place_weight):
        """One weighted purchase at `pool`; False when nothing more fits."""
        types = [u for u, i in self.units.items() if i['purchasable'] and self.weights.get(u, 0) > 0]
        while types:
            unit = _weighted(self.rng, types, lambda u: self.weights[u])
            spots = self.spots(unit, pool)
            if spots:
                self.buy(unit, _weighted(self.rng, spots, place_weight))
                return True
            types.remove(unit)
        return False


def _naval_territories(base, players, owner, scs, excluded):
    """{faction: {land id: sea zone}}: up to NAVAL_TERRITORIES coastal territories per faction (Strategic
    Centers, then the most valuable, first), each with its own adjacent sea zone -- the base map's sea
    deployment zone when free, else another free adjacent one -- no zone shared between factions."""
    terrs, adjacency = base.territories(), base.adjacency()
    preferred = base.sea_deployment()
    claimed = set(excluded)
    out = {f: {} for f in players}
    for f in sorted(players, key=lambda f: sum(1 for t, o in owner.items() if o == f
                                               and any(terrs[n]['type'] == 'sea' for n in adjacency[t]))):
        coastal = [t for t, o in owner.items() if o == f and any(terrs[n]['type'] == 'sea' for n in adjacency[t])]
        coastal.sort(key=lambda t: (t not in scs, -terrs[t]['value'], t))
        for t in coastal:
            if len(out[f]) >= NAVAL_TERRITORIES:
                break
            seas = [n for n in adjacency[t] if terrs[n]['type'] == 'sea' and n not in claimed]
            if not seas:
                continue
            zone = preferred.get(t) if preferred.get(t) in seas else seas[0]
            claimed.add(zone)
            out[f][t] = zone
    return out


def buy_faction_units(base, faction, holdings, scs, navals, options, rng, min_types, promotions, cap_bonus):
    """(units [(unit type, location, bought at)], promoted unit indexes, carryover MPC) for one faction."""
    weights = base.bot_settings()['unit_weights'].get(faction, {})
    units = base.units()
    garrison = _garrison_unit(units)
    best = None
    for attempt in range(UNIT_ATTEMPTS):
        b = _Buyer(base, faction, holdings, scs, navals, options, rng, weights, cap_bonus)
        b.budget = options['initial_mpc']
        if options['infantry_in_every_territory']:
            for t in sorted(holdings):
                if b.room(t) >= 1 and b.cost(garrison, t) <= b.budget:
                    b.add(garrison, t)
        non_sc = [t for t in holdings if t not in b.scs]
        for _ in range(options['extra_non_sc_units']):
            if not b.draw(non_sc, lambda t: b.terrs[t]['value'] + 1):
                break
        # the rest at Strategic Centers chosen at random, until none can take anything affordable
        sc_list = sorted(t for t in holdings if t in b.scs)
        while sc_list:
            t = rng.choice(sc_list)
            if not b.draw([t], lambda _: 1):
                sc_list.remove(t)
        types = {u for u, _, _ in b.bought}
        if best is None or len(types) > len({u for u, _, _ in best.bought}):
            best = b
        if len(types) >= min_types:
            break
    b = best
    placed = []
    for unit, tid, escort in b.bought:
        cat = units[unit]['category']
        where = b.navals[tid] if (cat == 'Sea' or escort) else tid
        placed.append((unit, where, tid))
    first = {}
    for i, (unit, _, tid) in enumerate(placed):
        if unit not in first or tid < placed[first[unit]][2]:
            first[unit] = i
    order = list(units)
    best_types = sorted(first, key=lambda u: (-weights.get(u, 0), order.index(u)))[:promotions]
    return placed, [first[u] for u in best_types], b.budget


def buy_neutral_units(base, holdings, scs, options, rng, cap_bonus):
    """[(unit type, location, bought at)] for the Neutral faction: land and air units only."""
    units = base.units()
    weights = {u: w for u, w in base.bot_settings().get('neutral_unit_weights', {}).items()
               if u in units and units[u]['category'] in ('Land', 'Air')}
    b = _Buyer(base, base.neutral_faction()['id'], holdings, scs, {}, options, rng, weights, cap_bonus)
    b.budget = options['neutral_budget']
    garrison = _garrison_unit(units)
    if options['neutral_infantry_in_every_territory']:
        for t in sorted(holdings):
            if b.room(t) >= 1 and b.cost(garrison, t) <= b.budget:
                b.add(garrison, t)
    while b.draw(list(holdings), lambda t: b.terrs[t]['value'] + 1 + (b.sc_bonus if t in b.scs else 0)):
        pass
    return [(unit, tid, tid) for unit, tid, _ in b.bought]


def _garrison_unit(units):
    land = [u for u, i in units.items() if i['purchasable'] and i['category'] == 'Land']
    musterers = [u for u in land if abilities.has(units, u, abilities.MUSTERING)]
    return min(musterers or land, key=lambda u: (units[u]['cost'], land.index(u)))


# ---- the generated modules -------------------------------------------------------------------------

def generate(base, players, options, rng, scenario_id='GEN_Scenario'):
    """(repository, scenario id, report) -- see the module docstring. `base`: the base scenario's
    GameConfig; `players`: the faction ids playing; `options`: resolve_options' result."""
    terrs = base.territories()
    owner = allocate_territory(base, players, options, rng)
    scs, notes = place_strategic_centers(base, players, owner, options, rng)
    neutral_id = base.neutral_faction()['id']

    std_setup, std_promos = base.initial_setup('standard')
    gen = std_setup.get('generation', {})
    cap_bonus = gen.get('cap_bonus', 3)
    min_types = gen.get('min_unit_types', 7)
    promotions = gen.get('promotions', 3)
    navals = _naval_territories(base, players, owner, set(scs), base.naval_deploy_excluded())

    by_location, promoted_ids, carryover = {}, [], {}
    for f in players:
        holdings = [t for t, o in owner.items() if o == f]
        placed, promoted, left = buy_faction_units(base, f, holdings, [t for t in scs if owner[t] == f], navals[f],
                                                   options, rng, min_types, promotions, cap_bonus)
        carryover[f] = left
        for i, (unit, where, bought) in enumerate(placed):
            uid = f'{f}-{i + 1:03d}'
            entry = {'id': uid, 'unit_type_id': unit, 'faction_id': f}
            if bought != where:
                entry['purchased_at'] = bought
            by_location.setdefault(where, []).append(entry)
            if i in promoted:
                promoted_ids.append(uid)
        types = {u for u, _, _ in placed}
        if len(types) < min_types:
            notes.append(f'{f} starts with {len(types)} unit types (fewer than {min_types})')

    neutral_holdings = [t for t, o in owner.items() if o == neutral_id]
    neutral_units = buy_neutral_units(base, neutral_holdings, [t for t in scs if owner[t] == neutral_id],
                                      options, rng, cap_bonus)
    neutral_by_location = {}
    for i, (unit, where, _) in enumerate(neutral_units):
        neutral_by_location.setdefault(where, []).append(
            {'id': f'{neutral_id}-{i + 1:03d}', 'unit_type_id': unit, 'faction_id': neutral_id})

    s = base.scenario
    ids = {k: f'GEN_{k}' for k in ('FactionAssignment', 'SCAssignment', 'StandardSetup', 'StandardPromotions',
                                    'NeutralSetup', 'NeutralPromotions', 'Rules')}
    fa = {'module_type': 'FactionAssignment', 'id': ids['FactionAssignment'], 'name': 'Generated territories',
          'map_values_id': s['map_values_id'], 'faction_set_id': s['faction_set_id'],
          'locations': [{'location_id': t, 'faction_id': owner[t],
                         'sea_deployment_location_id': base.sea_deployment().get(t)}
                        for t in terrs if terrs[t]['type'] == 'land']}
    sca = {'module_type': 'SCAssignment', 'id': ids['SCAssignment'], 'name': 'Generated Strategic Centers',
           'map_values_id': s['map_values_id'], 'sc_bonus': options['sc_bonus'], 'locations': scs}

    def setup_doc(key, name, located, extra=None):
        doc = {'module_type': 'InitialSetup', 'id': ids[key], 'name': name,
               'sc_assignment_id': ids['SCAssignment'], 'unit_set_id': s['unit_set_id'],
               'faction_assignment_id': ids['FactionAssignment'],
               'generation': {'budget': options['initial_mpc'], 'use_sc': True, 'min_unit_types': min_types,
                              'promotions': promotions, 'cap_bonus': cap_bonus, 'budget_tolerance': options['initial_mpc']}}
        doc.update(extra or {})
        doc['locations'] = [{'location_id': t, 'units': located[t]} for t in terrs if t in located]
        return doc

    std = setup_doc('StandardSetup', 'Generated setup', by_location, {'carryover_mpc': carryover})
    neu = setup_doc('NeutralSetup', 'Generated Neutral setup', neutral_by_location)
    std_p = {'module_type': 'UnitPromotions', 'id': ids['StandardPromotions'], 'name': 'Generated promotions',
             'initial_setup_id': ids['StandardSetup'], 'units': [{'unit_id': u, 'num_promotions': 1} for u in promoted_ids]}
    neu_p = {'module_type': 'UnitPromotions', 'id': ids['NeutralPromotions'], 'name': 'Generated Neutral promotions',
             'initial_setup_id': ids['NeutralSetup'], 'units': []}
    rules = copy.deepcopy(base.rule_set)
    rules['id'], rules['name'] = ids['Rules'], 'Generated rules'
    surrender = rules.setdefault('victory', {}).setdefault('surrender_rule', {})
    surrender['min_strategic_centers'] = options['min_scs_to_avoid_surrender']
    surrender['income_multiplier'] = options['surrender_income_multiplier']
    scenario = copy.deepcopy(s)
    scenario.update({'id': scenario_id, 'name': 'New scenario', 'faction_assignment_id': ids['FactionAssignment'],
                     'sc_assignment_id': ids['SCAssignment'], 'rule_set_id': ids['Rules'],
                     'setups': {'standard': {'initial_setup_id': ids['StandardSetup'],
                                             'unit_promotions_id': ids['StandardPromotions']},
                                'neutral': {'initial_setup_id': ids['NeutralSetup'],
                                            'unit_promotions_id': ids['NeutralPromotions']}},
                     'generated': {'base_scenario_id': s['id'], 'players': list(players), 'options': options,
                                   'notes': notes}})
    repo = OverlayRepository(base.repo, [scenario, fa, sca, std, neu, std_p, neu_p, rules])
    return repo, scenario_id, {'notes': notes, 'owner': owner, 'strategic_centers': scs}
