"""
Derive derived/faction_territory_profile.json: for each faction, its list
of owned land territories with the fields the setup-design tools need --
stacking cap, coastal flag, default sea zone, and whether it has a foreign
neighbor.

Run from the repo root, after compute_foreign_neighbors.py:
    python3 tools/compute_faction_profile.py
"""
import json

territories = json.load(open('data/territories.json'))['spaces']
adjacency = json.load(open('data/adjacency.json'))
foreign = json.load(open('derived/adjacency_foreign.json'))['foreign']
rules = json.load(open('data/rules.json'))['setup']

by_id = {s['id']: s for s in territories}
neighbors_ordered = {int(k): v for k, v in adjacency['neighbors_ordered'].items()}
SC_BONUS = rules['strategic_center_value_bonus']

land = [s for s in territories if s['type'] == 'land']
factions = sorted({s['faction'] for s in land})

profile = {fac: [] for fac in factions}
for terr in land:
    tid = terr['id']
    value = terr['value']
    sc = terr['strategic_center']
    cap = value + 3 + (SC_BONUS if sc else 0)
    # coastal / default sea zone: first sea-type neighbor in the graph's
    # original order (see tools/export_adjacency.py for why order matters)
    sea_neighbors = [n for n in neighbors_ordered.get(tid, []) if by_id[n]['type'] == 'sea']
    coastal = len(sea_neighbors) > 0
    sea_zone = sea_neighbors[0] if coastal else None
    sea_zone_name = by_id[sea_zone]['name'] if coastal else None
    profile[terr['faction']].append({
        'id': tid,
        'name': terr['name'],
        'value': value,
        'sc': sc,
        'cap': cap,
        'coastal': coastal,
        'sea_zone': sea_zone,
        'sea_zone_name': sea_zone_name,
        'has_foreign_neighbor': len(foreign.get(str(tid), [])) > 0,
    })

json.dump(profile, open('derived/faction_territory_profile.json', 'w'), indent=2)
print('wrote derived/faction_territory_profile.json:', {k: len(v) for k, v in profile.items()})
