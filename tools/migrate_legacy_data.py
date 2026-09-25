"""
One-time migration: builds the data/modules/ documents (docs/DATA_MODEL.md) from the
legacy data files -- data/territories.json, territory_shapes.json, adjacency.json,
adjacency_overrides.json, units.json, factions.json, rules.json, bot_settings.json
and data/scenarios/*.json.

Everything is carried over as is: the same ids, the same order, the same numbers.
Starting setups are converted by replaying the legacy placement (where each
purchased unit landed, including naval units and carrier escorts in their sea zone)
so every unit starts where it always has, in the same order.

    python tools/migrate_legacy_data.py
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.repository import ModuleRepository  # noqa: E402

DATA = os.path.join(ROOT, 'data')


def load(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return json.load(f)


def doc(module_type, module_id, name, **fields):
    d = {'module_type': module_type, 'id': module_id, 'name': name}
    d.update(fields)
    return d


# ---- Map --------------------------------------------------------------------------------------

def build_map(terr, shapes, adj, overrides, rules):
    excluded = set(rules['setup']['excluded_naval_zones'])
    locations = []
    for s in terr['spaces']:
        tid = s['id']
        loc = {
            'id': tid, 'name': s['name'], 'type': s['type'],
            'x': s['x'], 'y': s['y'], 'area_px': s['area_px'], 'bbox_w': s['bbox_w'], 'bbox_h': s['bbox_h'],
        }
        if tid in excluded:
            loc['naval_deploy_excluded'] = True
        loc['adjacency'] = adj['neighbors_ordered'][str(tid)]
        loc['boundary'] = shapes['shapes'][str(tid)]
        locations.append(loc)
    return doc(
        'Map', 'GC72_Map', 'Global Cataclysm: 1972 world map',
        _note=('Location x/y is the anchor point in the image (the flood-fill seed the boundary is '
               'extracted from); boundary (tools/extract_territory_shapes.py), area_px/bbox and adjacency '
               '(tools/compute_adjacency.py) are generated. naval_deploy_excluded: a sea zone that never '
               'hosts a naval deployment (landlocked).'),
        image='assets/base_map.png',
        width_px=terr['reference_image_width_px'], height_px=terr['reference_image_height_px'],
        topology='cylinder' if adj['wraps_east_west'] else 'flat',
        boundary_note=shapes['note'],
        boundary_epsilon_px=shapes['approx_epsilon_px'],
        adjacency_overrides={'note': overrides['_note'], 'add': overrides['add'], 'remove': overrides['remove']},
        locations=locations,
    )


# ---- Territory assignments ---------------------------------------------------------------------

def default_sea_zone(tid, terrs, neighbors):
    here = terrs[tid]
    seas = [n for n in neighbors[tid] if terrs[n]['type'] == 'sea']
    if not seas:
        return None
    return min(seas, key=lambda n: ((here['x'] - terrs[n]['x']) ** 2 + (here['y'] - terrs[n]['y']) ** 2) ** 0.5)


def build_assignments(terr, adj, rules):
    terrs = {s['id']: s for s in terr['spaces']}
    neighbors = {int(k): v for k, v in adj['neighbors_ordered'].items()}
    land = [s for s in terr['spaces'] if s['type'] == 'land']
    values = doc('MapValues', 'GC72_MapValues', 'GC72 territory values', map_id='GC72_Map',
                 locations=[{'location_id': s['id'], 'value': s['value']} for s in land])
    factions = doc(
        'FactionAssignment', 'GC72_FactionAssignment', 'GC72 starting territories',
        _note=('sea_deployment_location_id: the sea zone a coastal location sends its naval units to when a '
               'starting setup is generated (so neighbouring factions never share one); null inland.'),
        map_values_id='GC72_MapValues', faction_set_id='GC72_FactionSet',
        locations=[{'location_id': s['id'], 'faction_id': s['faction'],
                    'sea_deployment_location_id': default_sea_zone(s['id'], terrs, neighbors)} for s in land])
    scs = doc('SCAssignment', 'GC72_SCAssignment', 'GC72 Strategic Centers',
              _note="sc_bonus is added to a Strategic Center's value (income and per-turn deploy cap).",
              map_values_id='GC72_MapValues', sc_bonus=rules['setup']['strategic_center_value_bonus'],
              locations=[s['id'] for s in land if s['strategic_center']])
    return values, factions, scs


def build_faction_set(factions):
    return doc('FactionSet', 'GC72_FactionSet', 'GC72 factions',
               factions=[{'id': code, 'name': f['name'], 'color': f['color'], 'icon': None}
                         for code, f in factions['factions'].items()])


# ---- Abilities and units -----------------------------------------------------------------------

ABILITIES = [
    {'id': 'mustering', 'name': 'Mustering', 'description': 'Mustering: can be deployed in contested territory',
     'params': {}},
    {'id': 'dig_in', 'name': 'Dig In', 'description': 'Dig In: +{defense_bonus} defense when defending',
     'params': {'defense_bonus': 1}},
    {'id': 'heroic', 'name': 'Heroic', 'description': 'Heroic: eligible for {max_promotions} promotions',
     'params': {'max_promotions': 5}, 'listed': False},
    {'id': 'amphibious', 'name': 'Amphibious',
     'description': 'Amphibious: becomes a transport in sea spaces (the only land unit that can enter the water)',
     'params': {'transport_unit': 'Transport'}},
    {'id': 'blitz', 'name': 'Blitz',
     'description': 'Blitz: a combat move may pass through an undefended enemy territory, capturing it',
     'params': {}, 'listed': False},
    {'id': 'transport', 'name': 'Transport',
     'description': ('See transport & amphibious operations rules; a Mechanized Infantry unit becomes a transport '
                     'in a sea space, one transport per unit, with no move bonus'),
     'params': {}},
    {'id': 'air_superiority', 'name': 'Air Superiority',
     'description': 'Air Superiority: {attack_die} attack, {damage} damage in the pre-combat air superiority round',
     '_note': 'attack_die/damage null: the unit rolls its normal die and damage in that round. triggers_round: '
              'the round happens when both sides have aircraft and one of them has a unit that triggers it.',
     'params': {'attack_die': None, 'damage': None, 'triggers_round': False}},
    {'id': 'interception', 'name': 'Interception',
     'description': 'Interception: enemy aircraft cannot fly over a space it holds',
     'params': {}, 'listed': False},
    {'id': 'carrier_air_wing', 'name': 'Carrier Air Wing', 'description': 'Carrier Air Wing: carries up to {capacity} air units',
     'params': {'capacity': 3}},
    {'id': 'submerge', 'name': 'Submerge', 'description': "Submerge: can't engage or be attacked by aircraft",
     'params': {}},
    {'id': 'bombardment', 'name': 'Bombardment',
     'description': 'Bombardment: can do 1 bombardment round against land as Combat Move', 'params': {}},
    {'id': 'indiscriminate', 'name': 'Indiscriminate',
     'description': 'Indiscriminate: picks targets without the same-type preference', 'params': {}, 'listed': False},
]

UNIT_ABILITIES = {
    'Infantry': [('mustering', {}), ('dig_in', {}), ('heroic', {'max_promotions': 5})],
    'Mechanized Infantry': [('amphibious', {'transport_unit': 'Transport'}), ('blitz', {})],
    'Armor': [],
    'Fighter': [('air_superiority', {'attack_die': 'D10', 'damage': 3, 'triggers_round': True}), ('interception', {})],
    'Bomber': [('air_superiority', {'attack_die': 'D6', 'damage': 1, 'triggers_round': False}), ('indiscriminate', {})],
    'Aircraft Carrier': [('carrier_air_wing', {'capacity': 3})],
    'Submarine': [('submerge', {})],
    'Cruiser': [('bombardment', {})],
    'Transport': [('transport', {})],
}

ICONS = {
    'Infantry': 'person.svg', 'Mechanized Infantry': 'apc.svg', 'Armor': 'battle-tank.svg',
    'Fighter': 'jet-fighter.svg', 'Bomber': 'bomber.svg', 'Aircraft Carrier': 'carrier.svg',
    'Cruiser': 'cruiser.svg', 'Submarine': 'submarine.svg', 'Transport': 'cargo-ship.svg',
}
PLURALS = {'Infantry': 'Infantry', 'Mechanized Infantry': 'Mechanized Infantry', 'Armor': 'Armor'}
DISPLAY_ORDER = ['Infantry', 'Mechanized Infantry', 'Armor', 'Fighter', 'Bomber', 'Submarine', 'Cruiser',
                 'Aircraft Carrier']


def ability_text(ability_id, params):
    a = next(a for a in ABILITIES if a['id'] == ability_id)
    merged = dict(a['params'], **params)
    return a['description'].format(**merged)


def build_units(units, rules):
    catalog = doc('AbilityCatalog', 'GC72_Abilities', 'Engine ability library',
                  _note=('The special abilities the engine implements. A unit type opts into one by id and may set '
                         'its params (anything left out takes the default here). listed: false -- works, but is not '
                         "shown in the unit's ability list. A new ability needs engine code."),
                  abilities=ABILITIES)
    order = rules['combat']['resolution_order']
    types = []
    for name, u in units['units'].items():
        abilities = [{'id': a, 'params': p} if p else {'id': a} for a, p in UNIT_ABILITIES[name]]
        # The legacy special_abilities text must be exactly what the listed abilities render to.
        listed = [ability_text(a, p) for a, p in UNIT_ABILITIES[name]
                  if next(x for x in ABILITIES if x['id'] == a).get('listed', True)]
        if listed != u['special_abilities']:
            raise SystemExit(f'{name}: ability text {listed} != {u["special_abilities"]}')
        if 'air_superiority' in u:
            p = dict(UNIT_ABILITIES[name])['air_superiority']
            assert (p['attack_die'], p['damage']) == (u['air_superiority']['attack_die'], u['air_superiority']['damage'])
        heroic = dict(UNIT_ABILITIES[name]).get('heroic')
        expected_max = heroic['max_promotions'] if heroic else rules['promotion']['max_promotions']
        assert u.get('max_promotions', rules['promotion']['max_promotions']) == expected_max, name
        types.append({
            'id': name, 'name': name, 'category': u['category'],
            'cost': u['cost'], 'sc_cost': u['sc_cost'], 'attack_die': u['attack_die'], 'damage': u['damage'],
            'defense': u['defense'], 'hp': u['hp'], 'combat_move': u['combat_move'],
            'non_combat_move': u['non_combat_move'], 'purchasable': u['purchasable'],
            'land_order': order['land'].index(name) + 1 if name in order['land'] else None,
            'sea_order': order['sea'].index(name) + 1 if name in order['sea'] else None,
            'display_order': DISPLAY_ORDER.index(name) + 1 if name in DISPLAY_ORDER else None,
            'icon': ICONS[name],
            'plural': PLURALS.get(name, name + 's'),
            'abilities': abilities,
        })
    unit_set = doc('UnitSet', 'GC72_UnitSet', 'GC72 units',
                   _note=('Stats from the design doc. land_order/sea_order: position in a land/sea battle\'s '
                          'resolution order (null: does not fight there). display_order: the purchase panel. '
                          'icon: a file in assets/icons. plural: the name for several. Promotion steps the attack die up one size (max D12), '
                          '+1 defense (max 10), +1 HP; the default promotion cap is the rule set\'s, the heroic '
                          'ability raises it.'),
                   ability_catalog_id='GC72_Abilities',
                   category_icons={'Land': 'Infantry', 'Sea': 'Cruiser', 'Air': 'Fighter'},
                   unit_types=types)
    return catalog, unit_set


# ---- Rules -------------------------------------------------------------------------------------

def build_rules(rules):
    r = copy.deepcopy(rules)
    moved = []
    del r['combat']['resolution_order']
    moved.append("combat.resolution_order -> each unit type's land_order/sea_order (UnitSet)")
    del r['setup']['strategic_center_value_bonus']
    moved.append('setup.strategic_center_value_bonus -> SCAssignment.sc_bonus')
    del r['setup']['excluded_naval_zones']
    moved.append('setup.excluded_naval_zones -> Map locations[].naval_deploy_excluded')
    del r['map']
    moved.append('map -> the Map module (size, topology, adjacency)')
    del r['promotion']['max_promotions_note']
    r['promotion']['max_promotions_note'] = "the default cap: a unit type's heroic ability overrides it (Infantry 5)."
    return doc('RuleSet', 'GC72_Rules', 'Global Cataclysm: 1972 rules',
               _moved=moved, **{k: v for k, v in r.items()})


# ---- Starting setups ---------------------------------------------------------------------------

def replay_placement(scenario, units, terrs, neighbors):
    """Where each unit of each faction lands, in placement order -- the legacy
    engine/setup.py _place_faction_units/_apply_promotions logic. Returns
    ({faction: [(unit_type, location_id, bought_at), ...]}, {faction: [index into that list, ...]})."""
    placed, promoted = {}, {}
    name_to_id = {t['name']: tid for tid, t in terrs.items() if t['type'] == 'sea'}
    overrides = scenario.get('naval_deploy_overrides', {})

    def naval_zone(faction, buy_at, unit_type):
        zone_name = overrides.get(faction, {}).get(str(buy_at), {}).get(unit_type)
        return name_to_id[zone_name] if zone_name else default_sea_zone(buy_at, terrs, neighbors)

    for faction, entries in scenario['purchases'].items():
        escort_remaining = {}
        for esc in scenario.get('carrier_escorts', {}).get(faction, []):
            key = (esc['aircraft_tid'], esc['unit'])
            escort_remaining[key] = escort_remaining.get(key, 0) + esc['qty']
        out = placed.setdefault(faction, [])
        for entry in entries:
            buy_at = entry['territory_id']
            for u in entry['units']:
                unit_type = u['unit']
                category = units[unit_type]['category']
                for _ in range(u['qty']):
                    dest = buy_at
                    if category == 'Sea':
                        dest = naval_zone(faction, buy_at, unit_type)
                    elif category == 'Air' and escort_remaining.get((buy_at, unit_type), 0) > 0:
                        escort_remaining[(buy_at, unit_type)] -= 1
                        dest = naval_zone(faction, buy_at, 'Aircraft Carrier')
                    out.append((unit_type, dest, buy_at))
        done = set()
        for p in scenario.get('promotions', {}).get(faction, []):
            for i, (unit_type, _, bought) in enumerate(out):
                if unit_type == p['unit'] and bought == p['territory_id'] and i not in done:
                    done.add(i)
                    promoted.setdefault(faction, []).append(i)
                    break
    return placed, promoted


def build_setup(setup_id, promotions_id, name, scenario, generation, units, terrs, neighbors):
    placed, promoted = replay_placement(scenario, units, terrs, neighbors)
    by_location = {}
    unit_ids = {}
    for faction, rows in placed.items():
        for i, (unit_type, dest, bought) in enumerate(rows):
            uid = f'{faction}-{i + 1:03d}'
            unit_ids[(faction, i)] = uid
            entry = {'id': uid, 'unit_type_id': unit_type, 'faction_id': faction}
            if bought != dest:
                entry['purchased_at'] = bought
            by_location.setdefault(dest, []).append(entry)
    order = [s['id'] for s in terrs.values()]
    setup = doc('InitialSetup', setup_id, name,
                _note=scenario['_comment'],
                sc_assignment_id='GC72_SCAssignment', unit_set_id='GC72_UnitSet',
                faction_assignment_id='GC72_FactionAssignment',
                generation=generation,
                locations=[{'location_id': tid, 'units': by_location[tid]} for tid in order if tid in by_location])
    promos = doc('UnitPromotions', promotions_id, f'{name}: promotions', initial_setup_id=setup_id,
                 units=[{'unit_id': unit_ids[(f, i)], 'num_promotions': 1}
                        for f in placed for i in promoted.get(f, [])])
    return setup, promos


# ---- Bot settings ------------------------------------------------------------------------------

PRIMARY = [
    ('hold_sc', 'Hold Strategic Centers'), ('capture_sc', 'Capture Strategic Centers'),
    ('reinforce_contested', 'Reinforce contested territory'), ('punish_betrayers', 'Punish betrayers'),
    ('fill_gaps', 'Fill defensive gaps'), ('treasonous_capture', 'Treasonous capture'),
]
SECONDARY = [
    ('expand_territory', 'Expand territory'), ('hold_frontier', 'Hold the frontier'),
    ('control_oceans', 'Control the oceans'), ('pursue_sc_1', 'Pursue nearest enemy SC'),
    ('pursue_sc_2', 'Pursue second enemy SC'), ('pursue_sc_3', 'Pursue third enemy SC'),
    ('empty_land_grab', 'Grab empty land'),
]
FINAL = [('pursue_leftovers', 'Use leftover units')]


def build_bots(bot):
    objectives = doc('Objectives', 'GC72_Objectives', 'Strategy bot objectives',
                     _note='Each objective is a planner routine in engine/bots/planner.py; a new one needs code.',
                     objectives=[{'id': i, 'name': n, 'kind': 'primary'} for i, n in PRIMARY]
                     + [{'id': i, 'name': n, 'kind': 'secondary'} for i, n in SECONDARY]
                     + [{'id': i, 'name': n, 'kind': 'final'} for i, n in FINAL])
    order = doc('PrimaryObjectiveOrder', 'GC72_PrimaryOrder', 'Strategy bot planning order',
                _note=('The planner works through `steps` in order, each after taking `budget_share` of its planning '
                       'budget; treasonous_capture only runs when the bot means to betray its alliance this turn. '
                       'The secondary objectives come next (each taking secondary_budget_share, in an order drawn '
                       'by the strategy weights), then `final`.'),
                objectives_id='GC72_Objectives',
                steps=[{'objective_id': 'hold_sc', 'budget_share': 0.35},
                       {'objective_id': 'capture_sc', 'budget_share': 0.15},
                       {'objective_id': 'reinforce_contested', 'budget_share': 0.10},
                       {'objective_id': 'punish_betrayers', 'budget_share': 0.05},
                       {'objective_id': 'fill_gaps', 'budget_share': 0.05},
                       {'objective_id': 'treasonous_capture', 'budget_share': 0.10, 'when': 'treasonous'}],
                secondary_budget_share=0.12,
                final=[{'objective_id': 'pursue_leftovers', 'budget_share': 0.25}])
    strategies = []
    for style, objs in bot['thresholds'].items():
        rows = []
        for oid, t in objs.items():
            rows.append({'objective_id': oid, 'min_risk': t.get('min'), 'max_risk': t.get('max'), 'weight': t.get('weight')})
        strategies.append({'id': style, 'name': style, 'objectives': rows})
    strategies.append({'id': 'Variable', 'name': 'Variable', 'variable': True, 'objectives': []})
    thresholds = doc('StrategyThresholdSet', 'GC72_StrategyThresholds', 'Strategy bot thresholds',
                     _note=('min_risk/max_risk: the success probability (percent) an objective needs; weight: the odds '
                            'of a secondary objective being considered early. A variable strategy re-draws one of the '
                            'others every turn by the faction\'s strategy weights. distance_weights: the cost of a square '
                            'by kind, for the planner\'s travel estimates.'),
                     objectives_id='GC72_Objectives',
                     strategies=strategies,
                     distance_weights=bot['distance_costs'])
    weights = doc('FactionWeightSet', 'GC72_FactionWeights', 'Strategy bot faction weights',
                  _note='Odds for weighted random draws; 0 is never drawn.',
                  faction_set_id='GC72_FactionSet', unit_set_id='GC72_UnitSet',
                  strategy_threshold_set_id='GC72_StrategyThresholds',
                  factions=[{'faction_id': f,
                             'unit_weights': [{'unit_type_id': u, 'weight': w} for u, w in bot['unit_weights'][f].items()],
                             'strategy_weights': [{'strategy_id': s, 'weight': w} for s, w in bot['strategy_weights'][f].items()]}
                            for f in bot['unit_weights']])
    return objectives, order, thresholds, weights


def build_scenario():
    return doc('Scenario', 'GC72_Scenario', 'Global Cataclysm: 1972',
               map_id='GC72_Map', ability_catalog_id='GC72_Abilities', unit_set_id='GC72_UnitSet',
               faction_set_id='GC72_FactionSet', map_values_id='GC72_MapValues',
               faction_assignment_id='GC72_FactionAssignment', sc_assignment_id='GC72_SCAssignment',
               rule_set_id='GC72_Rules',
               setups={'standard': {'initial_setup_id': 'GC72_StandardSetup', 'unit_promotions_id': 'GC72_StandardPromotions'},
                       'defensive': {'initial_setup_id': 'GC72_DefensiveSetup', 'unit_promotions_id': 'GC72_DefensivePromotions'}},
               bots={'faction_weight_set_id': 'GC72_FactionWeights', 'strategy_threshold_set_id': 'GC72_StrategyThresholds',
                     'objectives_id': 'GC72_Objectives', 'primary_objective_order_id': 'GC72_PrimaryOrder'})


def main():
    terr = load('territories.json')
    shapes = load('territory_shapes.json')
    adj = load('adjacency.json')
    overrides = load('adjacency_overrides.json')
    units = load('units.json')
    factions = load('factions.json')
    rules = load('rules.json')
    bot = load('bot_settings.json')
    terrs = {s['id']: s for s in terr['spaces']}
    neighbors = {int(k): v for k, v in adj['neighbors_ordered'].items()}

    docs = [build_scenario(), build_map(terr, shapes, adj, overrides, rules)]
    docs += build_assignments(terr, adj, rules)
    docs.append(build_faction_set(factions))
    docs += build_units(units, rules)
    docs.append(build_rules(rules))
    docs += build_setup('GC72_StandardSetup', 'GC72_StandardPromotions', 'Standard setup (125 MPC)',
                        load('scenarios/starting_setup_125ipc.json'),
                        {'budget': 125, 'use_sc': True, 'min_unit_types': 7, 'promotions': 3,
                         'cap_bonus': 3, 'budget_tolerance': 0},
                        units['units'], terrs, neighbors)
    docs += build_setup('GC72_DefensiveSetup', 'GC72_DefensivePromotions', 'Defensive setup (100 MPC)',
                        load('scenarios/starting_setup_100ipc.json'),
                        {'budget': 100, 'use_sc': False, 'min_unit_types': 5, 'promotions': 0,
                         'cap_bonus': 0, 'budget_tolerance': 1},
                        units['units'], terrs, neighbors)
    docs += build_bots(bot)

    repo = ModuleRepository()
    for d in docs:
        repo.save(d)
        print('wrote', os.path.relpath(repo.path(d['module_type'], d['id']), ROOT))


if __name__ == '__main__':
    main()
