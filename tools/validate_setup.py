"""
Validate data/scenarios/starting_setup_200ipc.json against every setup rule
in data/rules.json: exact budget spend, per-territory stacking cap
(excluding naval units and carrier-escorted aircraft, same as the live
'Stacking Cap Check' formula in the Initial Setup tab), coastal-only naval
purchase, every Aircraft Carrier has an escorting Fighter/Bomber, no two
factions share a sea zone, and every land territory with a foreign
neighbor has at least 1 Infantry.

This is a port of the legacy validate_v5.py, reading from the new data/
and derived/ files instead of a handful of ad hoc JSON dumps. One
deliberate improvement over the legacy version: that script didn't have
access to carrier-escort data at all, so it counted an escorted aircraft
against its launch territory's land/air stacking cap -- which contradicts
the actual rule (an escorted aircraft deploys aboard the carrier, in the
carrier's sea zone, and is explicitly exempt from the land cap; see
data/rules.json setup.stacking_cap_excludes). Now that carrier_escorts is
real, portable data, this validator excludes escorted quantities from the
land/air cap count, matching what the Initial Setup tab's own formulas do.

Run from the repo root, after derived/faction_territory_profile.json and
derived/adjacency_foreign.json have been (re)built:
    python3 tools/validate_setup.py
Exits with status 1 if any errors are found, 0 otherwise.
"""
import json
import sys

units_data = json.load(open('data/units.json'))['units']
UNIT_COSTS = {name: {'type': info['category'], 'cost': info['cost'], 'sc_cost': info['sc_cost']}
              for name, info in units_data.items() if info['purchasable']}

scenario = json.load(open('data/scenarios/starting_setup_200ipc.json'))
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

NAVAL = {'Aircraft Carrier', 'Submarine', 'Cruiser'}
AIR = {'Fighter', 'Bomber'}
LAND = {'Infantry', 'Mechanized Infantry', 'Armor'}

errors = []
sea_zone_usage = {}
carriers_by_fac = {}

for fac, entries in DESIGN.items():
    escorted_qty = {}  # (tid, unit) -> qty riding a carrier, exempt from that tid's land cap
    for esc in CARRIER_ESCORTS.get(fac, []):
        key = (esc['aircraft_tid'], esc['unit'])
        escorted_qty[key] = escorted_qty.get(key, 0) + esc['qty']

    spend = 0
    for tid, units in entries:
        prof = prof_by_fac_id[fac][tid]
        is_sc = prof['sc']
        cap = prof['cap']
        land_air_here = 0
        for unit, qty in units:
            info = UNIT_COSTS[unit]
            spend += (info['sc_cost'] if is_sc else info['cost']) * qty
            if unit == 'Aircraft Carrier':
                carriers_by_fac.setdefault(fac, []).append(tid)
            if info['type'] == 'Sea':
                if not prof['coastal']:
                    errors.append(f"{fac}: naval {unit} at non-coastal {prof['name']}")
                zone = NAVAL_OVERRIDES.get(fac, {}).get(str(tid), {}).get(unit, prof['sea_zone_name'])
                sea_zone_usage.setdefault(zone, set()).add(fac)
            else:
                exempt = escorted_qty.get((tid, unit), 0)
                land_air_here += max(qty - exempt, 0)
        if land_air_here > cap:
            errors.append(f"{fac}: {prof['name']} ({tid}) {land_air_here}/{cap} land-air")
    if spend != BUDGET:
        errors.append(f'{fac}: spend {spend} != {BUDGET}')

foreign_ids = {int(k) for k, v in adjf['foreign'].items() if v}
for fac, entries in DESIGN.items():
    for tid, units in entries:
        if tid in foreign_ids:
            if 'Infantry' not in dict(units):
                errors.append(f'{fac}: {tid} foreign neighbor, no Infantry')

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

for zone, facs in sea_zone_usage.items():
    if len(facs) > 1:
        errors.append(f'COLLISION at {zone}: {facs}')

print('ERRORS:' if errors else 'No validation errors.')
for e in errors:
    print(' -', e)
print()
print('Carriers by faction:', carriers_by_fac)
print()
print('Sea zone usage:')
for z, f in sorted(sea_zone_usage.items()):
    print(' ', z, f)

sys.exit(1 if errors else 0)
