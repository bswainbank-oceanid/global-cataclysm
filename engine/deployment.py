"""
Deployment rules for initial deployments (every starting setup, and the setup generators) and
bot purchases -- not a human player's own purchases. See the rule set's `deployment` section.

The one rule so far: some unit types are never built or deployed on islands, however many;
another type is built instead (every Armor becomes Mechanized Infantry). An island is a landmass
of at most `island_size` (2) land territories -- land territories joined to each other by land
adjacency and to nothing else but sea.

Every function takes the game's data module (engine.data, a GameConfig, or a test's stand-in).
"""

_islands_cache = {}


def _rule(data_module):
    return (data_module.rules() or {}).get('deployment') or {}


def islands(data_module):
    """The land territory ids that are islands."""
    if hasattr(data_module, 'islands'):
        return data_module.islands()
    key = id(data_module)
    cached = _islands_cache.get(key)
    if cached is None or cached[0] is not data_module:  # (the object kept alongside: an id can be reused)
        cached = (data_module, compute_islands(data_module.territories(), data_module.adjacency(),
                                               _rule(data_module).get('island_size', 2)))
        _islands_cache[key] = cached
    return cached[1]


def compute_islands(territories, adjacency, max_land_territories):
    """Land territory ids whose landmass (joined by land-to-land adjacency) has at most
    `max_land_territories` territories."""
    land = [t for t, info in territories.items() if info['type'] == 'land']
    seen, out = set(), set()
    for start in land:
        if start in seen:
            continue
        mass, stack = [], [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            mass.append(cur)
            for n in adjacency.get(cur, []):
                if territories.get(n, {}).get('type') == 'land' and n not in seen:
                    seen.add(n)
                    stack.append(n)
        if len(mass) <= max_land_territories:
            out.update(mass)
    return out


def substitute(data_module, unit_type, location_id):
    """The unit type actually built when `unit_type` is deployed at `location_id` under the deployment
    rules: its island substitute on an island, else `unit_type` itself."""
    swap = _rule(data_module).get('island_substitutions') or {}
    if unit_type in swap and location_id in islands(data_module):
        return swap[unit_type]
    return unit_type
