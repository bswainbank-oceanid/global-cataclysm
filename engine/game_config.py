"""
GameConfig: one Scenario module resolved into everything a game reads -- the map,
unit set and abilities, factions, territory values/owners/Strategic Centers, rules,
starting setups and bot settings (docs/DATA_MODEL.md).

The engine sees the config through a few lookup views (units(), territories(),
adjacency(), factions(), rules(), ...), the same "data module" interface
engine/data.py exposes for the default scenario. Each view is built once, on first
use, and must be treated as read-only.
"""
from .repository import DEFAULT_SCENARIO_ID, default_repository


class GameConfig:
    def __init__(self, scenario_id=DEFAULT_SCENARIO_ID, repository=None):
        self.repo = repository or default_repository()
        self.scenario_id = scenario_id
        self._views = {}

    # ---- the modules ------------------------------------------------------------------------------

    def module(self, module_type, module_id):
        return self.repo.get(module_type, module_id)

    @property
    def scenario(self):
        return self.module('Scenario', self.scenario_id)

    @property
    def map(self):
        return self.module('Map', self.scenario['map_id'])

    @property
    def unit_set(self):
        return self.module('UnitSet', self.scenario['unit_set_id'])

    @property
    def ability_catalog(self):
        return self.module('AbilityCatalog', self.scenario['ability_catalog_id'])

    @property
    def faction_set(self):
        return self.module('FactionSet', self.scenario['faction_set_id'])

    @property
    def map_values(self):
        return self.module('MapValues', self.scenario['map_values_id'])

    @property
    def faction_assignment(self):
        return self.module('FactionAssignment', self.scenario['faction_assignment_id'])

    @property
    def sc_assignment(self):
        return self.module('SCAssignment', self.scenario['sc_assignment_id'])

    @property
    def rule_set(self):
        return self.module('RuleSet', self.scenario['rule_set_id'])

    def initial_setup(self, kind):
        """(InitialSetup, UnitPromotions) for the scenario's 'standard' or 'defensive' setup."""
        s = self.scenario['setups'][kind]
        return self.module('InitialSetup', s['initial_setup_id']), self.module('UnitPromotions', s['unit_promotions_id'])

    def bot_module(self, key):
        """One of the scenario's bot modules: 'faction_weight_set', 'strategy_threshold_set', 'objectives'
        or 'primary_objective_order'."""
        types = {'faction_weight_set': 'FactionWeightSet', 'strategy_threshold_set': 'StrategyThresholdSet',
                 'objectives': 'Objectives', 'primary_objective_order': 'PrimaryObjectiveOrder'}
        return self.module(types[key], self.scenario['bots'][f'{key}_id'])

    # ---- views ------------------------------------------------------------------------------------

    def _view(self, name, build):
        if name not in self._views:
            self._views[name] = build()
        return self._views[name]

    def sc_bonus(self):
        """What a Strategic Center adds to its territory's value."""
        return self.sc_assignment['sc_bonus']

    def map_info(self):
        """{width_px, height_px, wraps_east_west, image}"""
        m = self.map
        return {'width_px': m['width_px'], 'height_px': m['height_px'],
                'wraps_east_west': m['topology'] == 'cylinder', 'image': m['image']}

    def territories(self):
        """{location_id: {id, type, name, x, y, area_px, bbox_w, bbox_h, and for land: faction, value,
        strategic_center}} in map order."""
        def build():
            values = {r['location_id']: r['value'] for r in self.map_values['locations']}
            owners = {r['location_id']: r['faction_id'] for r in self.faction_assignment['locations']}
            scs = set(self.sc_assignment['locations'])
            out = {}
            for loc in self.map['locations']:
                t = {k: loc[k] for k in ('id', 'type', 'name', 'x', 'y', 'area_px', 'bbox_w', 'bbox_h')}
                if loc['type'] == 'land':
                    t['faction'] = owners.get(loc['id'])
                    t['value'] = values.get(loc['id'], 0)
                    t['strategic_center'] = loc['id'] in scs
                out[loc['id']] = t
            return out
        return self._view('territories', build)

    def adjacency(self):
        """{location_id: [neighbour id, ...]}"""
        return self._view('adjacency', lambda: {loc['id']: loc['adjacency'] for loc in self.map['locations']})

    def boundaries(self):
        """{location_id: [polygon, ...]}, each polygon a list of [x, y]."""
        return self._view('boundaries', lambda: {loc['id']: loc['boundary'] for loc in self.map['locations']})

    def naval_deploy_excluded(self):
        """Sea zones that never host a naval deployment."""
        return self._view('naval_excluded', lambda: [loc['id'] for loc in self.map['locations']
                                                     if loc.get('naval_deploy_excluded')])

    def sea_deployment(self):
        """{land location_id: sea zone its starting naval units deploy to (or None)}"""
        return self._view('sea_deployment', lambda: {r['location_id']: r.get('sea_deployment_location_id')
                                                     for r in self.faction_assignment['locations']})

    def factions(self):
        """{faction_id: {name, color, icon}} in faction-set order."""
        return self._view('factions', lambda: {f['id']: {'name': f['name'], 'color': f['color'], 'icon': f.get('icon')}
                                               for f in self.faction_set['factions']})

    def abilities(self):
        """{ability_id: catalog entry}"""
        return self._view('abilities', lambda: {a['id']: a for a in self.ability_catalog['abilities']})

    def units(self):
        """{unit_type_id: stats}: the unit set's fields, plus `abilities` resolved to {ability_id: params}
        (catalog defaults filled in) and `special_abilities`, the listed abilities' text."""
        def build():
            catalog = self.abilities()
            default_cap = self.rule_set['promotion']['max_promotions']
            out = {}
            for u in self.unit_set['unit_types']:
                abilities = {}
                for a in u.get('abilities', []):
                    abilities[a['id']] = dict(catalog[a['id']].get('params', {}), **a.get('params', {}))
                stats = {k: u[k] for k in ('category', 'cost', 'sc_cost', 'attack_die', 'damage', 'defense', 'hp',
                                          'combat_move', 'non_combat_move', 'purchasable')}
                stats['special_abilities'] = [catalog[a].get('description', a).format(**p)
                                              for a, p in abilities.items() if catalog[a].get('listed', True)]
                if 'air_superiority' in abilities:
                    stats['air_superiority'] = {'attack_die': abilities['air_superiority']['attack_die'],
                                                'damage': abilities['air_superiority']['damage']}
                stats['max_promotions'] = abilities['heroic']['max_promotions'] if 'heroic' in abilities else default_cap
                stats.update({
                    'name': u['name'], 'land_order': u.get('land_order'), 'sea_order': u.get('sea_order'),
                    'display_order': u.get('display_order'), 'icon': u.get('icon'),
                    'plural': u.get('plural') or u['name'] + 's', 'abilities': abilities,
                })
                out[u['id']] = stats
            return out
        return self._view('units', build)

    def rules(self):
        """The rule set, plus (for code that still looks there) the pieces that moved into other modules:
        combat.resolution_order, setup.strategic_center_value_bonus, setup.excluded_naval_zones, map."""
        def build():
            r = {k: v for k, v in self.rule_set.items() if k not in ('module_type', 'id', 'name', '_moved')}
            units = self.unit_set['unit_types']
            combat = dict(r['combat'])
            combat['resolution_order'] = {
                side: [u['id'] for u in sorted((u for u in units if u.get(f'{side}_order') is not None),
                                               key=lambda u: u[f'{side}_order'])]
                for side in ('land', 'sea')}
            r['combat'] = combat
            setup = dict(r['setup'])
            setup['strategic_center_value_bonus'] = self.sc_bonus()
            setup['excluded_naval_zones'] = self.naval_deploy_excluded()
            r['setup'] = setup
            info = self.map_info()
            r['map'] = {'reference_image_width_px': info['width_px'], 'reference_image_height_px': info['height_px'],
                        'wraps_east_west': info['wraps_east_west']}
            return r
        return self._view('rules', build)

    def bot_settings(self):
        """The strategy bots' settings in engine/bots/strategy_settings.py's shape: unit_weights,
        strategy_weights, thresholds, distance_costs, plus the planning order."""
        def build():
            weights = self.bot_module('faction_weight_set')
            thresholds = self.bot_module('strategy_threshold_set')
            out = {
                'unit_weights': {f['faction_id']: {w['unit_type_id']: w['weight'] for w in f['unit_weights']}
                                 for f in weights['factions']},
                'strategy_weights': {f['faction_id']: {w['strategy_id']: w['weight'] for w in f['strategy_weights']}
                                     for f in weights['factions']},
                'thresholds': {},
                'distance_costs': dict(thresholds['distance_weights']),
                'variable_strategies': [s['id'] for s in thresholds['strategies'] if s.get('variable')],
            }
            for s in thresholds['strategies']:
                if s.get('variable'):
                    continue
                rows = {}
                for o in s['objectives']:
                    row = {}
                    if o.get('weight') is not None:
                        row['weight'] = o['weight']
                    if o.get('min_risk') is not None:
                        row['min'] = o['min_risk']
                    if o.get('max_risk') is not None:
                        row['max'] = o['max_risk']
                    rows[o['objective_id']] = row
                out['thresholds'][s['id']] = rows
            order = self.bot_module('primary_objective_order')
            out['primary_order'] = order['steps']
            out['secondary_budget_share'] = order['secondary_budget_share']
            out['final_order'] = order['final']
            out['objectives'] = self.bot_module('objectives')['objectives']
            return out
        return self._view('bot_settings', build)
