"""
Read the hand-editable 'Initial Setup (125 MPC)' tab of exports/GC1972_Territories.xlsx back into
data/scenarios/starting_setup_125ipc.json -- the reverse of tools/build_setup_tab.py, so the setup
can be tuned in the spreadsheet (add, remove or change rows) and brought back into the game.

What is read, per faction block (the columns build_setup_tab.py writes):
  * purchase rows: Territory ID (A), Unit (E), Qty (F), Deploys To (I). Rows sharing a territory and unit
    are added up; a quantity of 0 is dropped.
  * a naval unit's Deploys To names its sea zone ("58. Labrador Sea"): a zone other than the one next to its
    territory becomes a naval_deploy_overrides entry.
  * a row whose Deploys To ends "(aboard Aircraft Carrier)" is a carrier escort: the carrier is the
    Aircraft Carrier row deploying to the same zone.
  * promoted-unit rows: Territory ID (A) and Unit (C).

    python tools/sync_scenario_from_xlsx.py            # write the scenario file
    python tools/sync_scenario_from_xlsx.py --check    # only report what would change

Run tools/validate_setup.py afterwards (this prints its own summary of differences first).
"""
import json
import sys

from openpyxl import load_workbook

XLSX = 'exports/GC1972_Territories.xlsx'
TAB = 'Initial Setup (125 MPC)'
SCENARIO = 'data/scenarios/starting_setup_125ipc.json'
FACTIONS = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']
NAVAL = {'Aircraft Carrier', 'Submarine', 'Cruiser'}
ABOARD = '(aboard Aircraft Carrier)'


ADJ = {int(k): set(v) for k, v in json.load(open('data/adjacency.json'))['neighbors_ordered'].items()}
SEA_IDS = {s['id'] for s in json.load(open('data/territories.json', encoding='utf-8'))['spaces'] if s['type'] == 'sea'}


def zone_id_of(text):
    """The sea zone id a Deploys To cell names ("58. Labrador Sea"); None for anything else -- notably the
    default text of a row ("77. Hawaii", the territory itself), which means the territory's own sea zone."""
    head = str(text).split('.', 1)[0].strip()
    return int(head) if head.isdigit() and int(head) in SEA_IDS else None


def read_tab(ws):
    """{faction: {'rows': [(tid, unit, qty, deploys_to)], 'promotions': [(tid, unit)]}}"""
    out = {}
    fac = None
    mode = None
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if isinstance(a, str):
            for f in FACTIONS:
                if a.endswith(f'({f})') and ws.cell(r + 1, 1).value == 'Territory ID' and ws.cell(r + 1, 5).value == 'Unit':
                    fac, mode = f, 'rows'
                    out[f] = {'rows': [], 'promotions': [], 'first_row': r}
                    break
            else:
                if a.startswith('Promoted Units') and fac:
                    mode = 'promo_header'
                elif a == 'Total':
                    mode = 'after_rows'
                elif a == 'Territory ID':
                    if mode == 'promo_header':
                        mode = 'promo'
                    continue
                elif mode == 'promo':
                    mode = None
            continue
        if a is None:
            if mode == 'promo':
                mode = None
            continue
        if mode == 'rows':
            unit, qty, deploys = ws.cell(r, 5).value, ws.cell(r, 6).value, ws.cell(r, 9).value
            if unit is None:
                continue
            out[fac]['rows'].append((int(a), str(unit).strip(), int(qty or 0), deploys, r))
        elif mode == 'promo':
            unit = ws.cell(r, 3).value
            if unit:
                out[fac]['promotions'].append((int(a), str(unit).strip(), r, ws.cell(r, 2).value))
    return out


def main():
    check_only = '--check' in sys.argv
    wb = load_workbook(XLSX, data_only=True)   # cached values: a Deploys To formula reads as its text
    tab = read_tab(wb[TAB])
    scenario = json.load(open(SCENARIO, encoding='utf-8'))
    profile = json.load(open('derived/faction_territory_profile.json'))
    by_id = {f: {r['id']: r for r in rows} for f, rows in profile.items()}
    names = {s['id']: s['name'] for s in json.load(open('data/territories.json', encoding='utf-8'))['spaces']}
    problems = []

    purchases, escorts, overrides, promotions = {}, {}, {}, {}
    for fac in FACTIONS:
        if fac not in tab:
            sys.exit(f'{TAB}: no block found for {fac}')
        bought = {}
        for tid, unit, qty, deploys, row in tab[fac]['rows']:
            if tid not in by_id[fac]:
                problems.append(f'{fac} row {row}: territory {tid} is not {fac}\'s')
                continue
            if qty > 0:
                bought.setdefault(tid, {})
                bought[tid][unit] = bought[tid].get(unit, 0) + qty
        purchases[fac] = bought

        def deploy_zone(tid, deploys, row):
            """The sea zone a naval unit bought at `tid` deploys to: the Deploys To cell's zone, or the territory's
            own when the cell is the default text or names a zone the territory does not border (the row was moved
            to another territory and the old zone left behind)."""
            default = by_id[fac][tid]['sea_zone']
            zone = zone_id_of(deploys)
            if zone is None:
                return default
            if zone not in ADJ.get(tid, ()):
                print(f'  note: {fac} row {row}: zone {zone}. {names[zone]} is not next to {tid}. {names[tid]}; using its own zone {default}. {names.get(default)}')
                return default
            return zone

        carriers = {}   # zone id -> carrier tid
        for tid, unit, qty, deploys, row in tab[fac]['rows']:
            if unit == 'Aircraft Carrier' and qty > 0 and tid in by_id[fac]:
                zone = deploy_zone(tid, deploys, row)
                carriers[zone] = tid
        for tid, unit, qty, deploys, row in tab[fac]['rows']:
            if qty <= 0 or tid not in by_id[fac]:
                continue
            if unit in NAVAL:
                zone = deploy_zone(tid, deploys, row)
                if zone != by_id[fac][tid]['sea_zone']:
                    overrides.setdefault(fac, {}).setdefault(str(tid), {})[unit] = names[zone]
            if isinstance(deploys, str) and deploys.rstrip().endswith(ABOARD):
                zone = zone_id_of(deploys)
                carrier = carriers.get(zone)
                if carrier is None:   # the zone is stale: take the carrier bought at the same territory
                    carrier = next((c for c in carriers.values() if c == tid), None)
                if carrier is None:
                    problems.append(f'{fac} row {row}: {unit} aboard a carrier, but no Aircraft Carrier row matches its zone {zone} or territory {tid}')
                    continue
                escorts.setdefault(fac, []).append({'carrier_tid': carrier, 'aircraft_tid': tid, 'unit': unit, 'qty': qty})
        promotions[fac] = []
        for tid, unit, row, label in tab[fac]['promotions']:
            if unit not in purchases[fac].get(tid, {}) or (label and label != names.get(tid) and not str(label).startswith('=')):
                # the ID and the territory name typed beside it disagree: go by the name, if that fits
                named = [t for t, n in names.items() if n == label and unit in purchases[fac].get(t, {})]
                if len(named) == 1 and unit not in purchases[fac].get(tid, {}):
                    print(f'  note: {fac} promotion row {row}: ID {tid} ({names.get(tid)}) has no {unit}; using the name "{label}" ({named[0]})')
                    tid = named[0]
            if unit not in purchases[fac].get(tid, {}):
                problems.append(f'{fac} promotion row {row}: no {unit} is bought at {tid}. {names.get(tid)}')
            promotions[fac].append({'territory_id': tid, 'unit': unit})

    new = dict(scenario)
    new['purchases'] = {
        fac: [{'territory_id': tid, 'units': [{'unit': u, 'qty': q} for u, q in sorted(units.items())]}
              for tid, units in sorted(purchases[fac].items())]
        for fac in FACTIONS}
    new['promotions'] = promotions
    new['carrier_escorts'] = escorts
    new['naval_deploy_overrides'] = overrides

    def flat(sc, fac):
        return {(e['territory_id'], u['unit']): u['qty'] for e in sc['purchases'][fac] for u in e['units']}
    for fac in FACTIONS:
        before, after = flat(scenario, fac), flat(new, fac)
        for key in sorted(set(before) | set(after)):
            if before.get(key, 0) != after.get(key, 0):
                print(f'  {fac} {key[0]}. {names[key[0]]}: {key[1]} {before.get(key, 0)} -> {after.get(key, 0)}')
        if scenario['promotions'].get(fac) != promotions[fac]:
            print(f'  {fac} promotions: {scenario["promotions"].get(fac)} -> {promotions[fac]}')
    if scenario.get('carrier_escorts') != escorts:
        print('  carrier escorts:', scenario.get('carrier_escorts'), '->', escorts)
    if scenario.get('naval_deploy_overrides') != overrides:
        print('  naval overrides:', scenario.get('naval_deploy_overrides'), '->', overrides)
    for p in problems:
        print('PROBLEM:', p)
    if check_only:
        return
    json.dump(new, open(SCENARIO, 'w', encoding='utf-8'), indent=2)
    print('wrote', SCENARIO)


if __name__ == '__main__':
    main()
