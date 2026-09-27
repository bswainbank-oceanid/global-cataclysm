"""
The New Scenario generator: from a base scenario (its map, territory values, unit set,
factions, rules and bot weights), scenario-wide options and each seat's settings, deal out a
fresh game -- which factions hold which territory, where the Strategic Centers are, and every
starting unit -- for the factions actually playing. See reference/GC72 New Scenario.odt and
docs/DATA_MODEL.md (ScenarioGenerator).

generate(base, seats, options, rng) returns (repository, scenario_id, report): an
OverlayRepository holding the generated Scenario and its generated modules in memory on
top of the base ones (nothing is written), the new Scenario's id, and notes on anything the
generator had to change. Everything is drawn from `rng`, so the same seed deals the same game.

Seats (resolve_seats): each playing faction has a territory value, initial MPC, units MPC,
promotions and Strategic Centers; the Neutral pool a territory value, units MPC, promotions and
Strategic Centers. The territory values together may be at most the map's total land value
(scaled down to fit when they are more); the rest of the map is Noncombatant.

The deal:
  1. Territory. In a random order fixed for the deal, the factions take turns drawing one
     unallocated land territory each -- weighted value + 1, + faction_weight if the territory is
     that faction's on the base map -- skipping any that would take it over its territory value,
     until every faction is full or nothing is left. The Neutral pool then draws the same way
     (value + 1) up to its territory value; whatever remains is Noncombatant.
  2. Strategic Centers. Round by round, each faction draws one of its territories (value + 1)
     at least sc_min_distance steps (the adjacency graph, land and sea) from every Strategic
     Center placed so far, its last one also at least sc_final_min_distance from its own others;
     then the Neutral ones, sc_min_distance from all. A deal that cannot place them all is
     retried; if it keeps failing, the distances are relaxed one step at a time (reported).
  3. Units, per faction, from its units MPC (spent out of its initial MPC): an Infantry (the
     garrison unit) in every territory (when infantry_in_every_territory), extra_non_sc_units
     more at territories that are not Strategic Centers (weighted value + 1), then the rest at
     randomly chosen Strategic Centers until nothing affordable fits; each unit type drawn by the
     faction's unit weights. The starting setup rules hold: a territory's stacking cap, naval units
     at a coastal territory's own sea zone (no zone shared between factions, none in an excluded
     zone), a carrier bought with an escorting aircraft, at least the base setup's minimum of unit
     types (retried; fewer accepted if it can't be reached). Its promotions go to one unit each of
     the highest-weighted types bought (a second unit of each, and so on, when there are more
     promotions than types). What is left of its initial MPC is its money at the start, on top of
     its first turn's income.
  4. Neutral units, from its units MPC: an Infantry in every Neutral territory (when
     neutral_infantry_in_every_territory), then land and air units drawn by the Neutral unit
     weights, placed weighted value + 1 (+ the SC bonus at a Strategic Center), until nothing
     fits; promotions as for a faction, by the Neutral unit weights.
"""
import copy
from collections import deque

from . import abilities, deployment
from .repository import OverlayRepository

# The scenario-wide options, in the order the launcher shows them: key -> (label, kind, min, max, help).
OPTIONS = {
    'faction_weight': ('Faction weight', 'int', 0, 100,
                       "Extra draw weight a faction gets for territory that is its own on the base map."),
    'sc_bonus': ('SC bonus', 'int', 0, 10, "What a Strategic Center adds to its territory's value."),
    'sc_min_distance': ('SC min distance', 'int', 0, 12,
                        'Fewest steps between any two Strategic Centers (relaxed if they cannot all fit).'),
    'sc_final_min_distance': ('Final SC min distance', 'int', 0, 12,
                              "Fewest steps between one of each player's Strategic Centers and its others."),
    'infantry_in_every_territory': ('Infantry everywhere', 'bool', None, None,
                                    "Start by putting an Infantry in each of a player's territories."),
    'extra_non_sc_units': ('Extra non-SC units', 'int', 0, 100,
                           "Units bought at territories that are not Strategic Centers, after the Infantry; "
                           'the rest of the budget goes to the Strategic Centers.'),
    'neutral_infantry_in_every_territory': ('Neutral Infantry everywhere', 'bool', None, None,
                                            'Start by putting an Infantry in each Neutral territory.'),
    'min_scs_to_avoid_surrender': ('SCs to avoid surrender', 'int', 0, 10,
                                   'A faction with fewer Strategic Centers can be forced to surrender (by one holding one of its own).'),
    'surrender_income_multiplier': ('Surrender income multiple', 'num', 1, 10,
                                    'A faction can force the surrender of one whose income is less than its own divided by this.'),
}

# Each seat's settings: key -> (label, min, max, help); a max of None is the map's total land value.
# The Neutral row has no initial MPC (it never spends money).
SEAT_SETTINGS = {
    'territory_value': ('Territory', 0, None, 'The territory value this seat is dealt (the seats together may hold '
                                              'at most the map\'s total; more is scaled down to fit).'),
    'initial_mpc': ('Initial MPC', 0, 1000, 'Money at the start: the starting units are bought out of it, and '
                                            'what is left is kept (on top of the first turn\'s income).'),
    'units_mpc': ('Units MPC', 0, 1000, 'What is spent on starting units (out of the initial MPC).'),
    'promotions': ('Promotions', 0, 20, 'Starting units promoted: one each of the highest-weighted types bought.'),
    'scs': ('SCs', 0, 10, 'Strategic Centers this seat starts with.'),
}
NEUTRAL_SEAT_SETTINGS = ('territory_value', 'units_mpc', 'promotions', 'scs')

SC_ATTEMPTS = 200       # deals of the Strategic Centers tried before relaxing the distances
UNIT_ATTEMPTS = 40      # purchase lists tried per faction for the unit-type minimum
NAVAL_TERRITORIES = 3   # coastal territories per faction that host its naval units


def land_value_total(base):
    return sum(t['value'] for t in base.territories().values() if t['type'] == 'land')


def option_specs(base, defaults):
    """[{key, label, kind, min, max, default, help}] for the launcher, maxima filled in from the base scenario."""
    total = land_value_total(base)
    out = []
    for key, (label, kind, lo, hi, help_text) in OPTIONS.items():
        default = defaults.get(key)
        out.append({'key': key, 'label': label, 'kind': kind, 'min': lo, 'max': total if hi is None else hi,
                    'default': default, 'help': help_text})
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
    """The generator's scenario-wide defaults with the launcher's choices on top."""
    out = {k: v for k, v in dict(defaults).items() if k in OPTIONS}
    out.update({k: v for k, v in (overrides or {}).items() if k in OPTIONS})
    for spec in option_specs(base, {}):
        v = out.get(spec['key'])
        if spec['kind'] == 'int' and isinstance(v, float) and v == int(v):
            out[spec['key']] = int(v)
    return out


def seat_specs(base, seat_defaults, neutral_defaults):
    """What the launcher needs for the seat settings: {total, players: [{key, label, min, max, default,
    help}], neutral: [...]}. A player's territory default is None: the map's total split evenly among
    the players (worked out as they change); the Neutral one None: whatever the players leave."""
    total = land_value_total(base)
    def specs(keys, defaults):
        return [{'key': k, 'label': SEAT_SETTINGS[k][0], 'min': SEAT_SETTINGS[k][1],
                 'max': total if SEAT_SETTINGS[k][2] is None else SEAT_SETTINGS[k][2],
                 'default': defaults.get(k), 'help': SEAT_SETTINGS[k][3]} for k in keys]
    return {'total': total, 'players': specs(list(SEAT_SETTINGS), seat_defaults),
            'neutral': specs(NEUTRAL_SEAT_SETTINGS, neutral_defaults)}


def check_seat(values, keys, total, who):
    """Problems with one seat's settings (defaults filled in; territory_value may be None: automatic)."""
    problems = []
    for k in keys:
        v = values.get(k)
        if k == 'territory_value' and v is None:
            continue
        hi = total if SEAT_SETTINGS[k][2] is None else SEAT_SETTINGS[k][2]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            problems.append(f'{who}: {SEAT_SETTINGS[k][0]} must be a whole number')
        elif not SEAT_SETTINGS[k][1] <= v <= hi:
            problems.append(f'{who}: {SEAT_SETTINGS[k][0]} must be between {SEAT_SETTINGS[k][1]} and {hi}')
    if 'initial_mpc' in keys and not problems and values['initial_mpc'] < values['units_mpc']:
        problems.append(f'{who}: Initial MPC must be at least the Units MPC')
    return problems


def resolve_seats(base, seat_defaults, neutral_defaults, players, player_overrides, neutral_override):
    """({'players': {faction: settings}, 'neutral': settings}, notes): each seat's settings with the
    defaults filled in -- a player's territory value, left automatic, is the map's total split evenly
    among the players (rounded down), the Neutral one whatever the players leave -- and the territory
    values scaled down (rounded down) when together they are more than the map's total."""
    total = land_value_total(base)
    notes = []
    seats = {}
    for f in players:
        v = {k: seat_defaults.get(k) for k in SEAT_SETTINGS}
        v.update({k: x for k, x in ((player_overrides or {}).get(f) or {}).items() if k in SEAT_SETTINGS})
        if v.get('territory_value') is None:
            v['territory_value'] = total // max(1, len(players))
        seats[f] = {k: int(x) for k, x in v.items()}
    neutral = {k: neutral_defaults.get(k) for k in NEUTRAL_SEAT_SETTINGS}
    neutral.update({k: x for k, x in (neutral_override or {}).items() if k in NEUTRAL_SEAT_SETTINGS})
    if neutral.get('territory_value') is None:
        neutral['territory_value'] = max(0, total - sum(v['territory_value'] for v in seats.values()))
    neutral = {k: int(x) for k, x in neutral.items()}
    asked = sum(v['territory_value'] for v in seats.values()) + neutral['territory_value']
    if asked > total:
        for v in list(seats.values()) + [neutral]:
            v['territory_value'] = v['territory_value'] * total // asked
        notes.append(f'territory values scaled down to fit the map ({asked} asked for, {total} on the map)')
    return {'players': seats, 'neutral': neutral}, notes


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


def allocate_territory(base, seats, options, rng):
    """{land id: owner} -- a player faction, the Neutral faction's id, or the Noncombatant's."""
    terrs = base.territories()
    land = sorted(t for t, info in terrs.items() if info['type'] == 'land')
    players = list(seats['players'])
    limit = {f: seats['players'][f]['territory_value'] for f in players}
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
            room = limit[f] - held[f]
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
    if seats['neutral']['territory_value'] > 0 and seats['neutral']['territory_value'] >= unclaimed:
        for t in remaining:
            owner[t] = neutral_id
        remaining = []
    else:
        room = seats['neutral']['territory_value']
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


def place_strategic_centers(base, seats, owner, options, rng):
    """(sorted SC ids, notes): the Strategic Centers, relaxing the distances when they can't all be placed."""
    terrs = base.territories()
    dist = _distances(base.adjacency())
    neutral_id = base.neutral_faction()['id']
    min_d, final_d = options['sc_min_distance'], options['sc_final_min_distance']
    notes = []
    while True:
        for _ in range(SC_ATTEMPTS):
            placed = _try_scs(terrs, dist, seats, owner, neutral_id, min_d, final_d, rng)
            if placed is not None:
                if (min_d, final_d) != (options['sc_min_distance'], options['sc_final_min_distance']):
                    notes.append(f'Strategic Center distances relaxed to {min_d} (final {final_d}) to fit them all')
                return sorted(placed), notes
        if min_d == 0 and final_d == 0:
            notes.append('not every Strategic Center could be placed')
            return [], notes
        min_d, final_d = max(0, min_d - 1), max(0, final_d - 1)


def _try_scs(terrs, dist, seats, owner, neutral_id, min_d, final_d, rng):
    players = list(seats['players'])
    placed, own = [], {f: [] for f in players}
    holdings = {f: [t for t, o in owner.items() if o == f] for f in players}
    want = {f: min(seats['players'][f]['scs'], len(holdings[f])) for f in players}
    for rnd in range(max(want.values(), default=0)):
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
    for _ in range(min(seats['neutral']['scs'], len(neutral_land))):
        cands = [t for t in neutral_land if t not in placed and all(dist[t].get(s, 999) >= min_d for s in placed)]
        pick = _weighted(rng, cands, lambda t: terrs[t]['value'] + 1)
        if pick is None:
            return None
        placed.append(pick)
    return placed


class _Buyer:
    """One faction's starting purchases, within its budget and the setup rules."""

    def __init__(self, base, faction, holdings, scs, navals, options, rng, weights, cap_bonus):
        self.terrs, self.units, self.data = base.territories(), base.units(), base
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
        if self.units[unit]['category'] == 'Land':
            unit = deployment.substitute(self.data, unit, tid)  # e.g. Mechanized Infantry, not Armor, on an island
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


def buy_faction_units(base, faction, seat, holdings, scs, navals, options, rng, min_types, cap_bonus):
    """(units [(unit type, location, bought at)], promoted unit indexes, MPC left of its initial MPC)."""
    weights = base.bot_settings()['unit_weights'].get(faction, {})
    units = base.units()
    garrison = _garrison_unit(units)
    best = None
    for attempt in range(UNIT_ATTEMPTS):
        b = _Buyer(base, faction, holdings, scs, navals, options, rng, weights, cap_bonus)
        b.budget = seat['units_mpc']
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
    spent = seat['units_mpc'] - b.budget
    return placed, _promoted(placed, weights, list(units), seat['promotions']), seat['initial_mpc'] - spent


def _promoted(placed, weights, unit_order, count):
    """Indexes into `placed` of the units promoted: one each of the highest-weighted unit types bought (at
    the lowest-id territory first), then a second unit of each, and so on, until `count` are chosen."""
    by_type = {}
    for i, (unit, _, bought) in enumerate(placed):
        by_type.setdefault(unit, []).append((bought, i))
    for unit in by_type:
        by_type[unit].sort()
    ranked = sorted(by_type, key=lambda u: (-weights.get(u, 0), unit_order.index(u)))
    out, rnd = [], 0
    while len(out) < count and any(rnd < len(by_type[u]) for u in ranked):
        for u in ranked:
            if len(out) >= count:
                break
            if rnd < len(by_type[u]):
                out.append(by_type[u][rnd][1])
        rnd += 1
    return out


def buy_neutral_units(base, seat, holdings, scs, options, rng, cap_bonus):
    """([(unit type, location, bought at)], promoted indexes) for the Neutral faction: land and air units only."""
    units = base.units()
    weights = {u: w for u, w in base.bot_settings().get('neutral_unit_weights', {}).items()
               if u in units and units[u]['category'] in ('Land', 'Air')}
    b = _Buyer(base, base.neutral_faction()['id'], holdings, scs, {}, options, rng, weights, cap_bonus)
    b.budget = seat['units_mpc']
    garrison = _garrison_unit(units)
    if options['neutral_infantry_in_every_territory']:
        for t in sorted(holdings):
            if b.room(t) >= 1 and b.cost(garrison, t) <= b.budget:
                b.add(garrison, t)
    while b.draw(list(holdings), lambda t: b.terrs[t]['value'] + 1 + (b.sc_bonus if t in b.scs else 0)):
        pass
    placed = [(unit, tid, tid) for unit, tid, _ in b.bought]
    return placed, _promoted(placed, weights, list(units), seat['promotions'])


def _garrison_unit(units):
    land = [u for u, i in units.items() if i['purchasable'] and i['category'] == 'Land']
    musterers = [u for u in land if abilities.has(units, u, abilities.MUSTERING)]
    return min(musterers or land, key=lambda u: (units[u]['cost'], land.index(u)))


# ---- the generated modules -------------------------------------------------------------------------

def generate(base, seats, options, rng, scenario_id='GEN_Scenario', notes=None):
    """(repository, scenario id, report) -- see the module docstring. `base`: the base scenario's
    GameConfig; `seats`: resolve_seats' result (the players are its factions); `options`:
    resolve_options' result; `notes`: resolve_seats' notes, carried into the report."""
    terrs = base.territories()
    players = list(seats['players'])
    owner = allocate_territory(base, seats, options, rng)
    scs, sc_notes = place_strategic_centers(base, seats, owner, options, rng)
    notes = list(notes or []) + sc_notes
    neutral_id = base.neutral_faction()['id']

    std_setup, std_promos = base.initial_setup('standard')
    gen = std_setup.get('generation', {})
    cap_bonus = gen.get('cap_bonus', 3)
    min_types = gen.get('min_unit_types', 7)
    navals = _naval_territories(base, players, owner, set(scs), base.naval_deploy_excluded())

    by_location, promoted_ids, carryover = {}, [], {}
    for f in players:
        holdings = [t for t, o in owner.items() if o == f]
        placed, promoted, left = buy_faction_units(base, f, seats['players'][f], holdings,
                                                   [t for t in scs if owner[t] == f], navals[f],
                                                   options, rng, min_types, cap_bonus)
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
    neutral_units, neutral_promoted = buy_neutral_units(base, seats['neutral'], neutral_holdings,
                                                        [t for t in scs if owner[t] == neutral_id],
                                                        options, rng, cap_bonus)
    neutral_by_location, neutral_promoted_ids = {}, []
    for i, (unit, where, _) in enumerate(neutral_units):
        uid = f'{neutral_id}-{i + 1:03d}'
        neutral_by_location.setdefault(where, []).append({'id': uid, 'unit_type_id': unit, 'faction_id': neutral_id})
        if i in neutral_promoted:
            neutral_promoted_ids.append(uid)

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

    budget = max([v['units_mpc'] for v in seats['players'].values()] + [seats['neutral']['units_mpc']])

    def setup_doc(key, name, located, extra=None):
        doc = {'module_type': 'InitialSetup', 'id': ids[key], 'name': name,
               'sc_assignment_id': ids['SCAssignment'], 'unit_set_id': s['unit_set_id'],
               'faction_assignment_id': ids['FactionAssignment'],
               'generation': {'budget': budget, 'use_sc': True, 'min_unit_types': min_types,
                              'promotions': 0, 'cap_bonus': cap_bonus, 'budget_tolerance': budget}}
        doc.update(extra or {})
        doc['locations'] = [{'location_id': t, 'units': located[t]} for t in terrs if t in located]
        return doc

    std = setup_doc('StandardSetup', 'Generated setup', by_location, {'carryover_mpc': carryover})
    neu = setup_doc('NeutralSetup', 'Generated Neutral setup', neutral_by_location)
    std_p = {'module_type': 'UnitPromotions', 'id': ids['StandardPromotions'], 'name': 'Generated promotions',
             'initial_setup_id': ids['StandardSetup'], 'units': [{'unit_id': u, 'num_promotions': 1} for u in promoted_ids]}
    neu_p = {'module_type': 'UnitPromotions', 'id': ids['NeutralPromotions'], 'name': 'Generated Neutral promotions',
             'initial_setup_id': ids['NeutralSetup'],
             'units': [{'unit_id': u, 'num_promotions': 1} for u in neutral_promoted_ids]}
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
                     'generated': {'base_scenario_id': s['id'], 'players': players, 'options': options,
                                   'seats': seats, 'notes': notes}})
    repo = OverlayRepository(base.repo, [scenario, fa, sca, std, neu, std_p, neu_p, rules])
    return repo, scenario_id, {'notes': notes, 'owner': owner, 'strategic_centers': scs}
