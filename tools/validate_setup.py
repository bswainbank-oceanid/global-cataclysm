"""
Validate a starting setup (an InitialSetup module, docs/DATA_MODEL.md) against every
setup rule in the rule set: the budget spent (exactly, or within the setup's
budget_tolerance), the per-territory stacking cap (EVERY unit bought at a territory
counts, including naval units and the aircraft on their carriers -- a starting-purchase
limit, not an in-game one; see the rule set's setup.stacking_cap_scope), naval units
bought only at coastal territories and deployed to a sea zone ADJACENT to where they
were bought, never to an excluded zone (the map's naval_deploy_excluded), every
carrier (carrier_air_wing ability) starting with at least one of its faction's
aircraft, no two factions sharing a sea zone (within a setup, and across the scenario's
standard and defensive setups, which can meet in one game), a land unit at every land
territory with a foreign neighbor (unless nothing fits there), and each faction using
at least min_unit_types unit types.

The parameters come from each setup's `generation` block: budget, use_sc (whether
Strategic Centers apply: their sc_cost discount and cap bonus), cap_bonus (cap = value +
cap_bonus, + the SC assignment's sc_bonus at a Strategic Center when use_sc),
min_unit_types, budget_tolerance.

Run from the repo root:
    python tools/validate_setup.py                      # both of the scenario's setups
    python tools/validate_setup.py --setup defensive
Exits with status 1 if any errors are found, 0 otherwise.
"""
import argparse
import sys

import tool_data
from engine import abilities


def check_setup(setup, used_zones):
    """Errors for one InitialSetup; records each sea zone's user in used_zones ({zone: {faction: setup id}})."""
    gen = setup['generation']
    use_sc, cap_bonus = gen['use_sc'], gen['cap_bonus']
    sc_bonus = tool_data.config().sc_bonus()
    units = tool_data.units()
    terrs = tool_data.config().territories()
    adj = tool_data.config().adjacency()
    excluded = set(tool_data.config().naval_deploy_excluded())
    name = lambda t: f"{t}. {terrs[t]['name']}"  # noqa: E731

    def is_sc(tid):
        return use_sc and terrs[tid].get('strategic_center')

    def cap(tid):
        return terrs[tid]['value'] + cap_bonus + (sc_bonus if is_sc(tid) else 0)

    errors = []
    spend, bought_here, types_used, land_at = {}, {}, {}, {}
    for loc in setup['locations']:
        where = loc['location_id']
        for u in loc['units']:
            fac, unit_type = u['faction_id'], u['unit_type_id']
            info = units[unit_type]
            bought = u.get('purchased_at', where)
            if terrs[bought]['type'] != 'land' or terrs[bought].get('faction') != fac:
                errors.append(f'{fac}: {unit_type} {u["id"]} bought at {name(bought)}, not a territory of {fac}')
                continue
            spend[fac] = spend.get(fac, 0) + (info['sc_cost'] if is_sc(bought) else info['cost'])
            bought_here[(fac, bought)] = bought_here.get((fac, bought), 0) + 1
            types_used.setdefault(fac, set()).add(unit_type)
            if terrs[where]['type'] == 'land':
                if info['category'] == 'Land':
                    land_at.setdefault(fac, set()).add(where)
                continue
            # a unit starting at sea: a ship, or an aircraft on its carrier
            if where not in adj[bought]:
                errors.append(f'{fac}: {unit_type} {u["id"]} bought at {name(bought)} starts in {name(where)}, '
                              f'which is not adjacent to it')
            if where in excluded:
                errors.append(f'{fac}: {unit_type} {u["id"]} deployed to excluded zone {name(where)}')
            used_zones.setdefault(where, {}).setdefault(fac, setup['id'])
            if info['category'] == 'Air' and not any(
                    o['faction_id'] == fac and abilities.has(units, o['unit_type_id'], abilities.CARRIER_AIR_WING)
                    for o in loc['units']):
                errors.append(f'{fac}: {unit_type} {u["id"]} in {name(where)} has no carrier there')
            if info['category'] == 'Sea' and abilities.has(units, unit_type, abilities.CARRIER_AIR_WING) and not any(
                    o['faction_id'] == fac and units[o['unit_type_id']]['category'] == 'Air' for o in loc['units']):
                errors.append(f'{fac}: {unit_type} {u["id"]} in {name(where)} has no aircraft')

    for (fac, tid), n in sorted(bought_here.items()):
        if n > cap(tid):
            errors.append(f'{fac}: {name(tid)} {n}/{cap(tid)}')

    for fac in tool_data.factions():
        s = spend.get(fac, 0)
        if s > gen['budget'] or gen['budget'] - s > gen.get('budget_tolerance', 0):
            errors.append(f'{fac}: spend {s} != {gen["budget"]} (tolerance {gen.get("budget_tolerance", 0)})')
        if len(types_used.get(fac, ())) < gen['min_unit_types']:
            errors.append(f'{fac}: only {len(types_used.get(fac, ()))} unit types (need >={gen["min_unit_types"]}): '
                          f'{sorted(types_used.get(fac, ()))}')
        for tid, t in terrs.items():
            if t['type'] != 'land' or t.get('faction') != fac or cap(tid) == 0:
                continue
            foreign = any(terrs[n]['type'] == 'land' and terrs[n].get('faction') != fac for n in adj[tid])
            if foreign and tid not in land_at.get(fac, ()):
                errors.append(f'{fac}: {name(tid)} has a foreign neighbor and no land unit')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
    parser.add_argument('--setup', choices=['standard', 'defensive', 'all'], default='all')
    args = parser.parse_args()
    tool_data.use_scenario(args.scenario)

    kinds = ['standard', 'defensive'] if args.setup == 'all' else [args.setup]
    used_zones, all_errors = {}, []
    for kind in kinds:
        setup, _ = tool_data.config().initial_setup(kind)
        errors = check_setup(setup, used_zones)
        print(f'{setup["id"]} ({kind}): ' + ('no validation errors.' if not errors else f'{len(errors)} error(s)'))
        for e in errors:
            print('  -', e)
        all_errors += errors
    terrs = tool_data.config().territories()
    for zone, facs in sorted(used_zones.items()):
        if len(facs) > 1:
            msg = f"sea zone {zone}. {terrs[zone]['name']} is used by several factions: {facs}"
            print('  -', msg)
            all_errors.append(msg)
    sys.exit(1 if all_errors else 0)


if __name__ == '__main__':
    main()
