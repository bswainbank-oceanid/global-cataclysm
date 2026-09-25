"""
Checks module documents (docs/DATA_MODEL.md): required fields and their types, ids
that must be unique, and every reference between modules -- so a bad edit is reported
here rather than surfacing as a crash mid-game.

validate_repository(repo) checks every module stored, including that each scenario's
modules fit together, and returns a list of problems (empty = valid).
"""
from .repository import MODULE_DIRS, ModuleNotFound

DIE_SIZES = ('D6', 'D8', 'D10', 'D12')
CATEGORIES = ('Land', 'Air', 'Sea')
INT = (int,)
NUM = (int, float)
STR = (str,)
BOOL = (bool,)


class _Checker:
    def __init__(self, repo):
        self.repo = repo
        self.problems = []

    def err(self, where, msg):
        self.problems.append(f'{where}: {msg}')

    def field(self, where, obj, key, types, optional=False, nullable=False):
        if key not in obj:
            if not optional:
                self.err(where, f'missing {key!r}')
            return None
        v = obj[key]
        if v is None:
            if not nullable:
                self.err(where, f'{key!r} is null')
            return None
        if isinstance(v, bool) and bool not in types:
            self.err(where, f'{key!r} should be {"/".join(t.__name__ for t in types)}, not {v!r}')
            return None
        if not isinstance(v, types):
            self.err(where, f'{key!r} should be {"/".join(t.__name__ for t in types)}, not {v!r}')
            return None
        return v

    def ref(self, where, module_type, module_id):
        if module_id is None:
            return None
        try:
            return self.repo.get(module_type, module_id)
        except ModuleNotFound:
            self.err(where, f'refers to missing {module_type} {module_id!r}')
        except ValueError as e:
            self.err(where, str(e))
        return None

    def unique(self, where, values, what):
        seen = set()
        for v in values:
            if v in seen:
                self.err(where, f'duplicate {what} {v!r}')
            seen.add(v)
        return seen

    # ---- one check per module type ----------------------------------------------------------------

    def check(self, doc):
        where = f'{doc.get("module_type")} {doc.get("id")}'
        self.field(where, doc, 'name', STR)
        getattr(self, f'_check_{MODULE_DIRS[doc["module_type"]]}')(where, doc)

    def _check_map(self, where, m):
        self.field(where, m, 'image', STR)
        self.field(where, m, 'width_px', INT)
        self.field(where, m, 'height_px', INT)
        if m.get('topology') not in ('flat', 'cylinder'):
            self.err(where, "topology must be 'flat' or 'cylinder'")
        locs = m.get('locations') or []
        if not locs:
            self.err(where, 'has no locations')
        ids = self.unique(where, [loc.get('id') for loc in locs], 'location id')
        types = {}
        for loc in locs:
            lw = f'{where} location {loc.get("id")}'
            self.field(lw, loc, 'id', INT)
            self.field(lw, loc, 'name', STR)
            if loc.get('type') not in ('land', 'sea'):
                self.err(lw, "type must be 'land' or 'sea'")
            types[loc.get('id')] = loc.get('type')
            for k in ('x', 'y'):
                self.field(lw, loc, k, NUM)
            for n in loc.get('adjacency', []):
                if n not in ids:
                    self.err(lw, f'adjacent to unknown location {n}')
                elif n == loc.get('id'):
                    self.err(lw, 'adjacent to itself')
            if not isinstance(loc.get('boundary'), list) or not loc.get('boundary'):
                self.err(lw, 'has no boundary polygons')
            if loc.get('naval_deploy_excluded') and loc.get('type') != 'sea':
                self.err(lw, 'naval_deploy_excluded is only for sea locations')
        adj = {loc.get('id'): set(loc.get('adjacency', [])) for loc in locs}
        for a, ns in adj.items():
            for b in ns:
                if b in adj and a not in adj[b]:
                    self.err(where, f'adjacency not symmetric: {a} lists {b} but not the reverse')
        for kind in ('add', 'remove'):
            for pair in m.get('adjacency_overrides', {}).get(kind, []):
                if not (isinstance(pair, list) and len(pair) >= 2 and pair[0] in ids and pair[1] in ids):
                    self.err(where, f'adjacency override {kind} {pair!r} does not name two locations')

    def _check_ability_catalog(self, where, c):
        abilities = c.get('abilities') or []
        self.unique(where, [a.get('id') for a in abilities], 'ability id')
        for a in abilities:
            aw = f'{where} ability {a.get("id")}'
            self.field(aw, a, 'id', STR)
            self.field(aw, a, 'name', STR)
            self.field(aw, a, 'description', STR)
            self.field(aw, a, 'params', (dict,))
            self.field(aw, a, 'listed', BOOL, optional=True)
            try:
                a.get('description', '').format(**a.get('params', {}))
            except (KeyError, IndexError) as e:
                self.err(aw, f'description names an unknown parameter {e}')

    def _check_unit_set(self, where, u):
        catalog = self.ref(where, 'AbilityCatalog', self.field(where, u, 'ability_catalog_id', STR))
        abilities = {a['id']: a for a in (catalog or {}).get('abilities', [])}
        types = u.get('unit_types') or []
        ids = self.unique(where, [t.get('id') for t in types], 'unit type id')
        for t in types:
            tw = f'{where} unit type {t.get("id")}'
            self.field(tw, t, 'id', STR)
            self.field(tw, t, 'name', STR)
            if t.get('category') not in CATEGORIES:
                self.err(tw, f'category must be one of {CATEGORIES}')
            purchasable = self.field(tw, t, 'purchasable', BOOL)
            self.field(tw, t, 'cost', INT, nullable=not purchasable)
            self.field(tw, t, 'sc_cost', INT, nullable=not purchasable)
            if t.get('attack_die') is not None and t['attack_die'] not in DIE_SIZES:
                self.err(tw, f'attack_die must be one of {DIE_SIZES} or null')
            self.field(tw, t, 'damage', INT, nullable=t.get('attack_die') is None)
            for k in ('defense', 'hp', 'combat_move', 'non_combat_move'):
                self.field(tw, t, k, INT)
            for k in ('land_order', 'sea_order', 'display_order'):
                self.field(tw, t, k, INT, optional=True, nullable=True)
            self.field(tw, t, 'icon', STR, optional=True, nullable=True)
            for a in t.get('abilities', []):
                if a.get('id') not in abilities:
                    self.err(tw, f'unknown ability {a.get("id")!r} (not in the ability catalog)')
                    continue
                for p in a.get('params', {}):
                    if p not in abilities[a['id']].get('params', {}):
                        self.err(tw, f'ability {a["id"]} has no parameter {p!r}')
                params = dict(abilities[a['id']].get('params', {}), **a.get('params', {}))
                if a['id'] == 'amphibious' and params.get('transport_unit') not in ids:
                    self.err(tw, f'amphibious transport_unit {params.get("transport_unit")!r} is not a unit type')
                if a['id'] == 'air_superiority' and params.get('attack_die') not in DIE_SIZES + (None,):
                    self.err(tw, 'air_superiority attack_die must be a die size (or null: its normal die)')
        for side in ('land_order', 'sea_order'):
            self.unique(where, [t[side] for t in types if t.get(side) is not None], side)
        for cat, unit in (u.get('category_icons') or {}).items():
            if unit not in ids:
                self.err(where, f'category_icons {cat} names unknown unit type {unit!r}')

    def _check_map_values(self, where, v):
        m = self.ref(where, 'Map', self.field(where, v, 'map_id', STR))
        land = {loc['id'] for loc in (m or {}).get('locations', []) if loc.get('type') == 'land'}
        rows = v.get('locations') or []
        got = self.unique(where, [r.get('location_id') for r in rows], 'location')
        for r in rows:
            if m and r.get('location_id') not in land:
                self.err(where, f'location {r.get("location_id")} is not a land location of the map')
            self.field(f'{where} location {r.get("location_id")}', r, 'value', INT)
        if m and land - got:
            self.err(where, f'no value for land locations {sorted(land - got)}')

    def _check_faction_set(self, where, f):
        facs = f.get('factions') or []
        self.unique(where, [x.get('id') for x in facs], 'faction id')
        for x in facs:
            fw = f'{where} faction {x.get("id")}'
            self.field(fw, x, 'id', STR)
            self.field(fw, x, 'name', STR)
            color = self.field(fw, x, 'color', STR)
            if color and not (color.startswith('#') and len(color) in (7, 9)):
                self.err(fw, f'color {color!r} should be #RRGGBB')
            self.field(fw, x, 'icon', STR, optional=True, nullable=True)

    def _map_of_values(self, where, values_id):
        values = self.ref(where, 'MapValues', values_id)
        return values, self.ref(where, 'Map', values['map_id']) if values else None

    def _check_faction_assignment(self, where, a):
        values, m = self._map_of_values(where, self.field(where, a, 'map_values_id', STR))
        fset = self.ref(where, 'FactionSet', self.field(where, a, 'faction_set_id', STR))
        facs = {x['id'] for x in (fset or {}).get('factions', [])}
        locs = {loc['id']: loc for loc in (m or {}).get('locations', [])}
        rows = a.get('locations') or []
        self.unique(where, [r.get('location_id') for r in rows], 'location')
        for r in rows:
            rw = f'{where} location {r.get("location_id")}'
            loc = locs.get(r.get('location_id'))
            if m and (loc is None or loc['type'] != 'land'):
                self.err(rw, 'is not a land location of the map')
                continue
            if fset and r.get('faction_id') not in facs:
                self.err(rw, f'unknown faction {r.get("faction_id")!r}')
            sea = r.get('sea_deployment_location_id')
            if sea is not None and loc is not None:
                if sea not in loc.get('adjacency', []) or locs.get(sea, {}).get('type') != 'sea':
                    self.err(rw, f'sea_deployment_location_id {sea} is not an adjacent sea zone')

    def _check_sc_assignment(self, where, a):
        values, m = self._map_of_values(where, self.field(where, a, 'map_values_id', STR))
        self.field(where, a, 'sc_bonus', INT)
        land = {loc['id'] for loc in (m or {}).get('locations', []) if loc.get('type') == 'land'}
        for tid in self.unique(where, a.get('locations') or [], 'location'):
            if m and tid not in land:
                self.err(where, f'Strategic Center {tid} is not a land location of the map')

    def _check_initial_setup(self, where, s):
        sc = self.ref(where, 'SCAssignment', self.field(where, s, 'sc_assignment_id', STR))
        units = self.ref(where, 'UnitSet', self.field(where, s, 'unit_set_id', STR))
        fa = self.ref(where, 'FactionAssignment', self.field(where, s, 'faction_assignment_id', STR))
        m = None
        if sc:
            _, m = self._map_of_values(where, sc['map_values_id'])
        types = {t['id']: t for t in (units or {}).get('unit_types', [])}
        facs = {r['faction_id'] for r in (fa or {}).get('locations', [])}
        locs = {loc['id']: loc for loc in (m or {}).get('locations', [])}
        self.unique(where, [loc.get('location_id') for loc in s.get('locations', [])], 'location')
        self.unique(where, [u.get('id') for loc in s.get('locations', []) for u in loc.get('units', [])], 'unit id')
        for loc in s.get('locations', []):
            lw = f'{where} location {loc.get("location_id")}'
            place = locs.get(loc.get('location_id'))
            if m and place is None:
                self.err(lw, 'is not a location of the map')
            for u in loc.get('units', []):
                uw = f'{lw} unit {u.get("id")}'
                t = types.get(u.get('unit_type_id'))
                if units and t is None:
                    self.err(uw, f'unknown unit type {u.get("unit_type_id")!r}')
                if fa and u.get('faction_id') not in facs:
                    self.err(uw, f'unknown faction {u.get("faction_id")!r}')
                if t and place:
                    if t['category'] == 'Sea' and place['type'] != 'sea':
                        self.err(uw, f'{t["id"]} placed on land')
                    if t['category'] == 'Land' and place['type'] != 'land':
                        self.err(uw, f'{t["id"]} placed at sea')
                bought = u.get('purchased_at')
                if bought is not None and m and locs.get(bought, {}).get('type') != 'land':
                    self.err(uw, f'purchased_at {bought} is not a land location')

    def _check_unit_promotions(self, where, p):
        setup = self.ref(where, 'InitialSetup', self.field(where, p, 'initial_setup_id', STR))
        ids = {u['id'] for loc in (setup or {}).get('locations', []) for u in loc.get('units', [])}
        self.unique(where, [u.get('unit_id') for u in p.get('units', [])], 'unit')
        for u in p.get('units', []):
            if setup and u.get('unit_id') not in ids:
                self.err(where, f'unit {u.get("unit_id")!r} is not in {setup["id"]}')
            n = self.field(f'{where} unit {u.get("unit_id")}', u, 'num_promotions', INT)
            if n is not None and n < 1:
                self.err(where, f'unit {u.get("unit_id")!r} num_promotions must be at least 1')

    def _check_rule_set(self, where, r):
        for section in ('setup', 'combat', 'movement', 'purchase', 'production', 'promotion', 'victory'):
            self.field(where, r, section, (dict,))
        promo = r.get('promotion', {})
        for k in ('xp_required', 'max_promotions', 'xp_for_surviving_a_round', 'xp_for_dealing_damage'):
            self.field(f'{where} promotion', promo, k, INT)

    def _check_objectives(self, where, o):
        objs = o.get('objectives') or []
        self.unique(where, [x.get('id') for x in objs], 'objective id')
        for x in objs:
            if x.get('kind') not in ('primary', 'secondary', 'final'):
                self.err(f'{where} objective {x.get("id")}', "kind must be 'primary', 'secondary' or 'final'")

    def _objective_ids(self, where, objectives_id, kinds):
        o = self.ref(where, 'Objectives', objectives_id)
        return {x['id'] for x in (o or {}).get('objectives', []) if x.get('kind') in kinds}, o is not None

    def _check_primary_objective_order(self, where, p):
        known, ok = self._objective_ids(where, self.field(where, p, 'objectives_id', STR), ('primary', 'final'))
        self.field(where, p, 'secondary_budget_share', NUM)
        for key in ('steps', 'final'):
            for step in p.get(key, []):
                if ok and step.get('objective_id') not in known:
                    self.err(where, f'{key}: unknown objective {step.get("objective_id")!r}')
                self.field(f'{where} {key} {step.get("objective_id")}', step, 'budget_share', NUM)

    def _check_strategy_threshold_set(self, where, t):
        known, ok = self._objective_ids(where, self.field(where, t, 'objectives_id', STR), ('primary', 'secondary'))
        o = self.ref(where, 'Objectives', t.get('objectives_id'))
        secondary = {x['id'] for x in (o or {}).get('objectives', []) if x.get('kind') == 'secondary'}
        strategies = t.get('strategies') or []
        self.unique(where, [s.get('id') for s in strategies], 'strategy id')
        for s in strategies:
            sw = f'{where} strategy {s.get("id")}'
            if s.get('variable'):
                continue
            rows = {r.get('objective_id'): r for r in s.get('objectives', [])}
            for oid, r in rows.items():
                if ok and oid not in known:
                    self.err(sw, f'unknown objective {oid!r}')
                for k in ('min_risk', 'max_risk', 'weight'):
                    self.field(f'{sw} {oid}', r, k, NUM, optional=True, nullable=True)
            for oid in secondary - set(rows):
                self.err(sw, f'no row for secondary objective {oid!r}')
            for oid in secondary & set(rows):
                if rows[oid].get('weight') is None:
                    self.err(sw, f'secondary objective {oid!r} needs a weight')
        dist = self.field(where, t, 'distance_weights', (dict,))
        for k in ('friendly', 'sea', 'enemy', 'allied'):
            if dist is not None:
                self.field(f'{where} distance_weights', dist, k, NUM)

    def _check_faction_weight_set(self, where, w):
        fset = self.ref(where, 'FactionSet', self.field(where, w, 'faction_set_id', STR))
        units = self.ref(where, 'UnitSet', self.field(where, w, 'unit_set_id', STR))
        thresholds = self.ref(where, 'StrategyThresholdSet', self.field(where, w, 'strategy_threshold_set_id', STR))
        facs = {x['id'] for x in (fset or {}).get('factions', [])}
        types = {t['id'] for t in (units or {}).get('unit_types', [])}
        strategies = {s['id'] for s in (thresholds or {}).get('strategies', [])}
        seen = self.unique(where, [f.get('faction_id') for f in w.get('factions', [])], 'faction')
        if fset and facs - seen:
            self.err(where, f'no weights for factions {sorted(facs - seen)}')
        for f in w.get('factions', []):
            fw = f'{where} faction {f.get("faction_id")}'
            if fset and f.get('faction_id') not in facs:
                self.err(fw, 'unknown faction')
            for r in f.get('unit_weights', []):
                if units and r.get('unit_type_id') not in types:
                    self.err(fw, f'unknown unit type {r.get("unit_type_id")!r}')
                self.field(fw, r, 'weight', NUM)
            for r in f.get('strategy_weights', []):
                if thresholds and r.get('strategy_id') not in strategies:
                    self.err(fw, f'unknown strategy {r.get("strategy_id")!r}')
                self.field(fw, r, 'weight', NUM)

    def _check_scenario(self, where, s):
        refs = {
            'map_id': 'Map', 'ability_catalog_id': 'AbilityCatalog', 'unit_set_id': 'UnitSet',
            'faction_set_id': 'FactionSet', 'map_values_id': 'MapValues',
            'faction_assignment_id': 'FactionAssignment', 'sc_assignment_id': 'SCAssignment', 'rule_set_id': 'RuleSet',
        }
        got = {k: self.ref(where, t, self.field(where, s, k, STR)) for k, t in refs.items()}
        for kind in ('standard', 'defensive'):
            setup = (s.get('setups') or {}).get(kind)
            if not setup:
                self.err(where, f'no {kind} setup')
                continue
            ini = self.ref(where, 'InitialSetup', setup.get('initial_setup_id'))
            promos = self.ref(where, 'UnitPromotions', setup.get('unit_promotions_id'))
            if ini and got['sc_assignment_id'] and ini['sc_assignment_id'] != s['sc_assignment_id']:
                self.err(where, f'{kind} setup uses SC assignment {ini["sc_assignment_id"]}, the scenario {s["sc_assignment_id"]}')
            if ini and ini['unit_set_id'] != s.get('unit_set_id'):
                self.err(where, f'{kind} setup uses unit set {ini["unit_set_id"]}, the scenario {s.get("unit_set_id")}')
            if promos and ini and promos['initial_setup_id'] != ini['id']:
                self.err(where, f'{kind} promotions are for {promos["initial_setup_id"]}, not {ini["id"]}')
        bot_refs = {'faction_weight_set_id': 'FactionWeightSet', 'strategy_threshold_set_id': 'StrategyThresholdSet',
                    'objectives_id': 'Objectives', 'primary_objective_order_id': 'PrimaryObjectiveOrder'}
        for k, t in bot_refs.items():
            self.ref(where, t, (s.get('bots') or {}).get(k))
        # The pieces must describe the same map, factions and units.
        if got['map_values_id'] and got['map_values_id']['map_id'] != s.get('map_id'):
            self.err(where, 'map values are for a different map')
        for k in ('faction_assignment_id', 'sc_assignment_id'):
            if got[k] and got[k]['map_values_id'] != s.get('map_values_id'):
                self.err(where, f'{k} uses different map values')
        if got['faction_assignment_id'] and got['faction_assignment_id']['faction_set_id'] != s.get('faction_set_id'):
            self.err(where, 'faction assignment uses a different faction set')
        if got['unit_set_id'] and got['unit_set_id']['ability_catalog_id'] != s.get('ability_catalog_id'):
            self.err(where, 'unit set uses a different ability catalog')


def validate_repository(repo):
    c = _Checker(repo)
    for module_type in MODULE_DIRS:
        for module_id in repo.ids(module_type):
            try:
                doc = repo.get(module_type, module_id)
            except ValueError as e:
                c.problems.append(str(e))
                continue
            c.check(doc)
    return c.problems

