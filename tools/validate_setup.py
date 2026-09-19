"""
Validate a starting-setup scenario against every setup rule in
data/rules.json: exact budget spend, per-territory stacking cap (EVERY
unit purchased at a territory counts, including naval units and
carrier-escorted aircraft -- a starting-purchase limit, not an in-game
one; see data/rules.json setup.stacking_cap_scope), coastal-only naval
purchase, every naval deployment lands in a sea zone ADJACENT to the
territory it was bought at (checked against data/adjacency.json, so a
hand-picked naval_deploy_overrides zone can't silently go stale when
adjacency changes), every Aircraft Carrier has an escorting Fighter/Bomber,
no two factions share a sea zone, every land territory with a foreign neighbor
has at least 1 land unit (Infantry, Mechanized Infantry, or Armor -- not
necessarily Infantry), and each faction purchases at least N of the 8
purchasable unit types (setup.unit_diversity_rule).

Defaults to the canonical 200-IPC/SC scenario. --no-sc validates a
ruleset that ignores every territory's strategic_center flag entirely:
cap becomes flat value+<--cap-bonus> (instead of value+3, +2 more if SC)
and every cost uses the unit's plain 'cost' field (never 'sc_cost') --
use it for scenarios like data/scenarios/starting_setup_100ipc.json. A
territory whose cap comes out to 0 is exempt from the foreign-border
mandatory-land-unit rule (nothing fits there at all).

Run from the repo root, after derived/faction_territory_profile.json and
derived/adjacency_foreign.json have been (re)built:
    python3 tools/validate_setup.py
    python3 tools/validate_setup.py --scenario data/scenarios/starting_setup_100ipc.json --no-sc --cap-bonus 0 --min-types 5 --budget-tolerance 1
Exits with status 1 if any errors are found, 0 otherwise.
"""
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--scenario', default='data/scenarios/starting_setup_200ipc.json')
parser.add_argument('--no-sc', action='store_true', help='ignore strategic_center: flat value+cap-bonus cap, no sc_cost discount')
parser.add_argument('--cap-bonus', type=int, default=2, help='with --no-sc, cap = value + this (0 = no bonus at all)')
parser.add_argument('--min-types', type=int, default=6, help='minimum distinct unit types required per faction')
parser.add_argument('--budget-tolerance', type=int, default=0,
                     help='allow up to this many IPC unspent (small change) instead of requiring an exact match')
parser.add_argument('--exclude-zone', type=int, action='append', default=[43],
                     help='sea zone id that must never host a naval deployment (repeatable). '
                          'Zone 43 (Caspian Sea) is excluded by default -- a permanent rule, not scenario-specific.')
args = parser.parse_args()

units_data = json.load(open('data/units.json'))['units']
UNIT_COSTS = {name: {'type': info['category'], 'cost': info['cost'], 'sc_cost': info['sc_cost']}
              for name, info in units_data.items() if info['purchasable']}

scenario = json.load(open(args.scenario))
BUDGET = scenario['budget_ipc']
DESIGN = {
    fac: [(entry['territory_id'], [(u['unit'], u['qty']) for u in entry['units']]) for entry in entries]
    for fac, entries in scenario['purchases'].items()
}
CARRIER_ESCORTS = scenario['carrier_escorts']
NAVAL_OVERRIDES = scenario['naval_deploy_overrides']

profile = json.load(open('derived/faction_territory_profile.json'))
prof_by_fac_id = {fac: {r['id']: r for r in rows} for fac, rows in profile.items()}

adjf = json.load(open('derived/adjacency_foreign.json'))

# Sea zone NAMES aren't unique on this map (e.g. two "Southern Ocean" zones
# in different oceans) -- collisions must be checked by zone id, not name,
# or two factions using distinct, non-adjacent same-named zones get
# flagged as sharing one. naval_deploy_overrides stores a name string (not
# an id), so an override is resolved to whichever same-named zone is
# nearest the deploying territory.
territories_data = json.load(open('data/territories.json'))['spaces']
SPACE_BY_ID = {s['id']: s for s in territories_data}
SEA_IDS_BY_NAME = {}
for s in territories_data:
    if s['type'] == 'sea':
        SEA_IDS_BY_NAME.setdefault(s['name'], []).append(s['id'])

_adj_raw = json.load(open('data/adjacency.json'))['neighbors_ordered']
ADJ = {int(k): set(v) for k, v in _adj_raw.items()}


def _dist(a, b):
    return ((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2) ** 0.5

def resolve_zone_id(from_tid, zone_name):
    candidates = SEA_IDS_BY_NAME.get(zone_name, [])
    if not candidates:
        return zone_name  # unknown name; fall back to name-keyed (shouldn't happen)
    if len(candidates) == 1:
        return candidates[0]
    origin = SPACE_BY_ID[from_tid]
    return min(candidates, key=lambda zid: _dist(origin, SPACE_BY_ID[zid]))

NAVAL = {'Aircraft Carrier', 'Submarine', 'Cruiser'}
AIR = {'Fighter', 'Bomber'}
LAND = {'Infantry', 'Mechanized Infantry', 'Armor'}

errors = []
sea_zone_usage = {}
carriers_by_fac = {}

for fac, entries in DESIGN.items():
    spend = 0
    for tid, units in entries:
        prof = prof_by_fac_id[fac][tid]
        is_sc = prof['sc'] and not args.no_sc
        cap = (prof['value'] + args.cap_bonus) if args.no_sc else prof['cap']
        units_here = 0
        for unit, qty in units:
            info = UNIT_COSTS[unit]
            spend += (info['sc_cost'] if is_sc else info['cost']) * qty
            if unit == 'Aircraft Carrier':
                carriers_by_fac.setdefault(fac, []).append(tid)
            if info['type'] == 'Sea':
                if not prof['coastal']:
                    errors.append(f"{fac}: naval {unit} at non-coastal {prof['name']}")
                override_name = NAVAL_OVERRIDES.get(fac, {}).get(str(tid), {}).get(unit)
                zone_id = resolve_zone_id(tid, override_name) if override_name else prof['sea_zone']
                if zone_id not in ADJ.get(tid, ()):
                    errors.append(f"{fac}: {unit} bought at {prof['name']} ({tid}) deploys to "
                                  f"{zone_id}. {SPACE_BY_ID[zone_id]['name']}, which is not adjacent to it "
                                  f"(adjacent seas: {[(n, SPACE_BY_ID[n]['name']) for n in sorted(ADJ.get(tid, ())) if SPACE_BY_ID[n]['type'] == 'sea']})")
                if zone_id in args.exclude_zone:
                    errors.append(f"{fac}: {unit} deployed to excluded zone {zone_id}. {SPACE_BY_ID[zone_id]['name']}")
                sea_zone_usage.setdefault(zone_id, set()).add(fac)
            # every unit purchased at this territory counts against its cap --
            # a starting-purchase limit, not an in-game one (setup.stacking_cap_scope)
            units_here += qty
        if units_here > cap:
            errors.append(f"{fac}: {prof['name']} ({tid}) {units_here}/{cap}")
    if spend > BUDGET or BUDGET - spend > args.budget_tolerance:
        errors.append(f'{fac}: spend {spend} != {BUDGET} (tolerance {args.budget_tolerance})')

foreign_ids = {int(k) for k, v in adjf['foreign'].items() if v}
for fac, entries in DESIGN.items():
    units_by_tid = dict(entries)
    for tid, prof in prof_by_fac_id.get(fac, {}).items():
        if tid not in foreign_ids:
            continue
        cap = (prof['value'] + args.cap_bonus) if args.no_sc else prof['cap']
        if cap == 0:
            continue  # nothing can fit here at all -- exempt from the rule
        units = units_by_tid.get(tid, [])
        if not any(u in LAND and qty > 0 for u, qty in units):
            errors.append(f'{fac}: {tid} foreign neighbor, no land unit')

for fac, entries in DESIGN.items():
    types_used = {u for _, units in entries for u, qty in units if qty > 0}
    if len(types_used) < args.min_types:
        errors.append(f'{fac}: only {len(types_used)} unit types purchased (need >={args.min_types}): {sorted(types_used)}')

for fac, entries in DESIGN.items():
    ed = {t: dict(u) for t, u in entries}
    for tid in carriers_by_fac.get(fac, []):
        units_here = ed.get(tid, {})
        has_air = any(units_here.get(u, 0) >= 1 for u in AIR)
        if not has_air:
            errors.append(f'{fac}: Aircraft Carrier at {tid} has no aircraft')
    # every carrier must actually appear as a carrier_tid in the escort list
    escorted_carriers = {esc['carrier_tid'] for esc in CARRIER_ESCORTS.get(fac, [])}
    for tid in carriers_by_fac.get(fac, []):
        if tid not in escorted_carriers:
            errors.append(f'{fac}: Aircraft Carrier at {tid} has no registered carrier_escorts entry')

for zone_id, facs in sea_zone_usage.items():
    if len(facs) > 1:
        errors.append(f"COLLISION at {zone_id}. {SPACE_BY_ID[zone_id]['name']}: {facs}")

print('ERRORS:' if errors else 'No validation errors.')
for e in errors:
    print(' -', e)
print()
print('Carriers by faction:', carriers_by_fac)
print()
print('Sea zone usage:')
for zid, f in sorted(sea_zone_usage.items(), key=lambda kv: SPACE_BY_ID[kv[0]]['name']):
    print(f"  {zid}. {SPACE_BY_ID[zid]['name']}", f)

sys.exit(1 if errors else 0)
