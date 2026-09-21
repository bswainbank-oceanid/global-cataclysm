"""
Generate a starting-setup scenario from scratch: territory ownership from
data/territories.json, doctrine emphasis from data/factions.json's 'focus'
lists. The core generate_scenario() function is parameterized so it can
produce variant rulesets (see tools/generate_scenario_100ipc.py); this
file's own __main__ block only points at tools/generate_scenario_weighted.py, which
produces the canonical 125-MPC scenario (this algorithm is what the 100-MPC Defensive
scenario still uses).

Algorithm per faction:
  1. Baseline garrison: 1 unit of the faction's primary land type, at every
     owned territory (budget allowing) or just the foreign-bordering ones
     (guarantees the foreign-border land-unit rule for free either way).
     Primary land type is Infantry by default, overridden to Mechanized
     Infantry for factions whose doctrine focus explicitly names it ahead
     of Infantry (NAA, UE) -- see LAND_PRIMARY_OVERRIDE. A faction whose
     primary isn't Infantry still gets a small token Infantry presence
     (the "safety valve") purely so the leftover-budget upgrade trick in
     phase 6 always has a target.
  2. Pick a spread of naval territories per faction (coastal, up to
     NAVAL_SPREAD_TARGET), ordered best-cap-first (Strategic Centers rank
     first when the ruleset uses them) so later phases naturally
     concentrate naval purchases there and only spill over to the
     lower-priority ones once cap headroom runs out -- "concentrate at the
     best spot, sprinkle elsewhere" rather than one hub. Each chosen
     territory claims its own sea zone; zones are assigned collision-free
     across all 6 factions (an override is used only if a faction runs out
     of non-colliding coastal candidates).
  3. Diversity seed: 1 unit of each doctrine-selected type (the primary
     land type is skipped -- already everywhere from phase 1) so every
     faction definitely uses all its selected types even before weighted
     spend. FORCE_INCLUDE_TYPES adds a type regardless of doctrine rank
     (currently: AAC always gets >=1 Fighter).
  4. If a Navy type is selected, buy an Aircraft Carrier + 1 escort
     Fighter/Bomber at the hub as a bundle (registers carrier_escorts).
  5. Weighted round-robin: spend the bulk of the remaining budget across
     the selected types, weighted toward doctrine focus (the safety-valve
     Infantry and any FORCE_INCLUDE type carry deliberately low weight so
     they stay minor), respecting each territory's starting-purchase cap
     (counts EVERY unit bought there, naval and carrier-escorted air
     included; see data/rules.json setup.stacking_cap_scope) and
     naval-at-hub-only.
  6. Parity fix (only matters when the ruleset has no SC cost variants):
     every purchasable unit costs an even number of IPC except Submarine
     (9), so hitting an exact budget with an odd number of Submarines
     purchased is impossible under this rule's even-cost table (SC
     variants add several odd-cost options, sidestepping the issue --
     see cost_at). Toggle one Submarine at a naval territory to fix parity
     before the exact-fill pass.
  7. Exact-fill: a doctrine-priority pass mops up any large remainder,
     then a cheapest-first pass nails the exact small remainder, then (if
     a residual 1-2 IPC still remains) the Infantry -> Mechanized Infantry
     upgrade rule uses it up.
  8. Promote the N unit types with the faction's highest doctrine weight
     (one pick per type, at the lowest-id territory that has it); N=0
     skips this entirely.

Run from the repo root, after derived/faction_territory_profile.json has
been (re)built:
    python3 tools/generate_scenario.py
Writes data/scenarios/starting_setup_125ipc.json. Always re-run
tools/build_all.py (or at least build_setup_tab.py + validate_setup.py)
afterward.
"""
import json

PURCHASABLE = None  # populated by generate_scenario() on first call
LAND_TYPES = ['Infantry', 'Mechanized Infantry', 'Armor']
AIR_TYPES = ['Fighter', 'Bomber']
NAVAL_TYPES = ['Aircraft Carrier', 'Submarine', 'Cruiser']

FOCUS_MAP = {
    'Air': AIR_TYPES,
    'Navy': NAVAL_TYPES,
    'Infantry': ['Infantry'],
    'Mechanized Infantry': ['Mechanized Infantry'],
    'Armor': ['Armor'],
    'Bombers': ['Bomber'],
}
DEFAULT_FILL = ['Infantry', 'Mechanized Infantry', 'Armor', 'Submarine', 'Cruiser', 'Fighter', 'Bomber', 'Aircraft Carrier']

FACTION_ORDER = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']

# NAA and UE's doctrine focus names Mechanized Infantry ahead of any plain
# Infantry mention -- their primary land garrison type follows suit.
# UER and PAF's focus names Infantry first (explicitly or by default), so
# they keep Infantry primary, Mechanized Infantry secondary.
LAND_PRIMARY_OVERRIDE = {'NAA': 'Mechanized Infantry', 'UE': 'Mechanized Infantry'}
# Extra type(s) guaranteed regardless of doctrine rank, at low weight.
FORCE_INCLUDE_TYPES = {'AAC': ['Fighter']}

NAVAL_SPREAD_TARGET = 3

# Permanent, unconditional rule for every scenario this generator produces:
# Caspian Sea (zone id 43) never hosts a naval deployment, default or
# override. Landlocked in play terms despite touching several coastal
# territories on the map -- excluded regardless of what a caller passes.
ALWAYS_EXCLUDED_NAVAL_ZONE_IDS = {43}


def generate_scenario(*, budget, use_sc, min_types, promotions_count,
                       garrison_all_territories, out_path, comment,
                       cap_bonus=2, excluded_naval_zone_ids=frozenset()):
    """Build and write one scenario JSON. See module docstring for the
    algorithm. use_sc=False ignores every territory's strategic_center
    flag: cap becomes flat value+cap_bonus (no +2 SC bonus stacked on top)
    and every cost uses the unit's plain 'cost' (never 'sc_cost'). A
    territory whose cap comes out to 0 (cap_bonus=0 and value=0) is never
    given any unit -- including the foreign-border mandatory land unit,
    which simply doesn't apply where nothing fits.
    excluded_naval_zone_ids: sea zone ids that must never host a naval
    deployment, default or override (e.g. a zone the map designer wants
    kept empty for flavor/geography reasons)."""
    global PURCHASABLE
    territories = json.load(open('data/territories.json'))['spaces']
    units_data = json.load(open('data/units.json'))['units']
    factions_data = json.load(open('data/factions.json'))['factions']
    profile = json.load(open('derived/faction_territory_profile.json'))

    PURCHASABLE = {name: info for name, info in units_data.items() if info['purchasable']}

    def cost_at(unit, is_sc):
        info = PURCHASABLE[unit]
        return info['sc_cost'] if (use_sc and is_sc) else info['cost']

    def cap_of(row):
        return row['cap'] if use_sc else row['value'] + cap_bonus

    all_sea_spaces = sorted((s for s in territories if s['type'] == 'sea'), key=lambda s: s['id'])

    # ---- doctrine ranking + selected types ----
    primary_land_type = {fac: LAND_PRIMARY_OVERRIDE.get(fac, 'Infantry') for fac in FACTION_ORDER}
    used_types = {}
    weight_of = {}
    ranked_by_fac = {}
    for fac in FACTION_ORDER:
        focus = factions_data[fac]['focus']
        ranked = []
        for cat in focus:
            for t in FOCUS_MAP.get(cat, []):
                if t not in ranked:
                    ranked.append(t)
        for t in DEFAULT_FILL:
            if t not in ranked:
                ranked.append(t)
        ranked_by_fac[fac] = ranked

        primary = primary_land_type[fac]
        safety_valve = ['Infantry'] if primary != 'Infantry' else []
        force_extra = FORCE_INCLUDE_TYPES.get(fac, [])
        core = [primary] + safety_valve
        exclude = set(core) | set(force_extra)
        rest_ranked = [t for t in ranked if t not in exclude]
        need = max(0, min_types - len(core) - len(force_extra))
        core_and_rest = core + rest_ranked[:need]
        chosen = core_and_rest + force_extra
        used_types[fac] = chosen

        weight_seq = [7, 6, 5, 4, 3, 2, 1]
        w = {t: (weight_seq[i] if i < len(weight_seq) else 1) for i, t in enumerate(core_and_rest)}
        for t in safety_valve:
            w[t] = 1  # minor presence only -- doctrine deprioritizes it
        for t in force_extra:
            w[t] = 2  # guaranteed present, but not a doctrine emphasis
        weight_of[fac] = w

    # ---- naval territory spread, zone-collision-free ----
    # Pre-claiming excluded_naval_zone_ids keeps them out of both the
    # direct-pick and override-fallback paths below without extra logic.
    claimed_zone_ids = set(excluded_naval_zone_ids) | ALWAYS_EXCLUDED_NAVAL_ZONE_IDS
    naval_territories = {}  # fac -> [{'id','name','sc','cap','zone','override'}, ...]
    for fac in sorted(FACTION_ORDER, key=lambda f: len([r for r in profile[f] if r['coastal']])):
        candidates = [r for r in profile[fac] if r['coastal']]
        candidates.sort(key=lambda r: (not (use_sc and r['sc']), -cap_of(r), -r['value']))
        chosen_list = []
        for c in candidates:
            if len(chosen_list) >= NAVAL_SPREAD_TARGET:
                break
            if c['sea_zone'] in claimed_zone_ids:
                continue
            claimed_zone_ids.add(c['sea_zone'])
            chosen_list.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'cap': cap_of(c),
                                 'zone': c['sea_zone_name'], 'override': False})
        used_ids = {x['id'] for x in chosen_list}
        for c in candidates:
            if len(chosen_list) >= NAVAL_SPREAD_TARGET or len(chosen_list) >= len(candidates):
                break
            if c['id'] in used_ids:
                continue
            free = [z for z in all_sea_spaces if z['id'] not in claimed_zone_ids]
            if not free:
                break
            claimed_zone_ids.add(free[0]['id'])
            chosen_list.append({'id': c['id'], 'name': c['name'], 'sc': c['sc'], 'cap': cap_of(c),
                                 'zone': free[0]['name'], 'override': True})
            used_ids.add(c['id'])
        naval_territories[fac] = chosen_list

    primary_naval = {fac: naval_territories[fac][0] for fac in FACTION_ORDER}

    # ---- state ----
    purchases = {fac: {} for fac in FACTION_ORDER}
    cap_used = {fac: {} for fac in FACTION_ORDER}
    spend = {fac: 0 for fac in FACTION_ORDER}
    cap_by_tid = {fac: {r['id']: cap_of(r) for r in profile[fac]} for fac in FACTION_ORDER}
    sc_by_tid = {fac: {r['id']: r['sc'] for r in profile[fac]} for fac in FACTION_ORDER}
    foreign_by_tid = {fac: {r['id']: r['has_foreign_neighbor'] for r in profile[fac]} for fac in FACTION_ORDER}
    carrier_escorts = {}

    def headroom(fac, tid):
        return cap_by_tid[fac][tid] - cap_used[fac].get(tid, 0)

    def add_unit(fac, tid, unit, qty):
        is_sc = sc_by_tid[fac][tid]
        purchases[fac].setdefault(tid, {})
        purchases[fac][tid][unit] = purchases[fac][tid].get(unit, 0) + qty
        cap_used[fac][tid] = cap_used[fac].get(tid, 0) + qty
        spend[fac] += cost_at(unit, is_sc) * qty

    # ---- Phase 1: baseline garrison ----
    # garrison_all_territories=True (the full-budget case) uses each
    # faction's doctrine-primary land type -- budget comfortably covers
    # every territory at that price. garrison_all_territories=False (the
    # tight-budget case) covers only the mandatory foreign-bordering
    # territories, and uses plain Infantry (the cheapest land type)
    # regardless of doctrine primary, since a smaller budget spread over
    # a still-sizable mandatory-coverage list can't always afford the
    # doctrine-primary type (e.g. Mechanized Infantry) at every one of
    # them. Both variants respect the budget -- a territory that can't
    # afford even one Infantry is simply left ungarrisoned rather than
    # overspending (should not happen at 100 IPC in practice).
    baseline_unit = {}
    for fac in FACTION_ORDER:
        baseline_unit[fac] = primary_land_type[fac] if garrison_all_territories else 'Infantry'
        for r in profile[fac]:
            if not garrison_all_territories and not r['has_foreign_neighbor']:
                continue
            if cap_of(r) < 1:
                continue
            tid = r['id']
            cost = cost_at(baseline_unit[fac], sc_by_tid[fac][tid])
            if spend[fac] + cost <= budget:
                add_unit(fac, tid, baseline_unit[fac], 1)

    # ---- Phase 2: diversity seed -- 1 of each selected type not already everywhere ----
    # Aircraft Carrier is skipped here -- it's bought together with its
    # mandatory escort as one atomic bundle in Phase 3, since buying just
    # the carrier under a budget squeeze (leaving no room for the escort)
    # would violate the carrier_escort_rule.
    for fac in FACTION_ORDER:
        land_air_by_cap = sorted(profile[fac], key=lambda r: -cap_of(r))
        for t in used_types[fac]:
            if t == baseline_unit[fac] or t == 'Aircraft Carrier':
                continue
            if t in NAVAL_TYPES:
                for nt in naval_territories[fac]:
                    if headroom(fac, nt['id']) >= 1 and spend[fac] + cost_at(t, nt['sc']) <= budget:
                        add_unit(fac, nt['id'], t, 1)
                        break
            else:
                for r in land_air_by_cap:
                    if headroom(fac, r['id']) >= 1 and spend[fac] + cost_at(t, r['sc']) <= budget:
                        add_unit(fac, r['id'], t, 1)
                        break

    # ---- Phase 3: Aircraft Carrier + escort bundle at the primary naval
    # territory -- bought together, atomically, so a budget squeeze can
    # never leave a carrier without its mandatory escort. ----
    for fac in FACTION_ORDER:
        if 'Aircraft Carrier' not in used_types[fac]:
            continue
        hub_tid = primary_naval[fac]['id']
        escort_unit = 'Fighter' if 'Fighter' in used_types[fac] else 'Bomber'
        is_sc = sc_by_tid[fac][hub_tid]
        bundle_cost = cost_at('Aircraft Carrier', is_sc) + cost_at(escort_unit, is_sc)
        if headroom(fac, hub_tid) >= 2 and spend[fac] + bundle_cost <= budget:
            add_unit(fac, hub_tid, 'Aircraft Carrier', 1)
            add_unit(fac, hub_tid, escort_unit, 1)
            carrier_escorts.setdefault(fac, []).append(
                {'carrier_tid': hub_tid, 'aircraft_tid': hub_tid, 'unit': escort_unit, 'qty': 1})
        else:
            # bundle didn't fit (tight budget) -- Aircraft Carrier drops
            # from this faction's plan entirely (never bought alone), so
            # swap in the next-best doctrine type to hold the diversity
            # floor, and seed 1 unit of it now.
            used_types[fac] = [t for t in used_types[fac] if t != 'Aircraft Carrier']
            weight_of[fac].pop('Aircraft Carrier', None)
            replacement = next((t for t in ranked_by_fac[fac]
                                 if t not in used_types[fac] and t != 'Aircraft Carrier'), None)
            if replacement:
                used_types[fac].append(replacement)
                weight_of[fac][replacement] = 2
                cand = naval_territories[fac] if replacement in NAVAL_TYPES else \
                    sorted(profile[fac], key=lambda r: -cap_of(r))
                for r in cand:
                    if headroom(fac, r['id']) >= 1 and spend[fac] + cost_at(replacement, r['sc']) <= budget:
                        add_unit(fac, r['id'], replacement, 1)
                        break

    # ---- Phase 4: weighted round-robin bonus spend ----
    def weighted_queue(weights):
        items = []
        for t, w in weights.items():
            for i in range(w):
                items.append((i / w, t))
        items.sort()
        return [t for _, t in items]

    for _pass in range(8):
        for fac in FACTION_ORDER:
            territories_by_cap = sorted(profile[fac], key=lambda r: -cap_of(r))
            for t in weighted_queue(weight_of[fac]):
                if t == 'Aircraft Carrier':
                    continue
                cand = naval_territories[fac] if t in NAVAL_TYPES else territories_by_cap
                for r in cand:
                    tid = r['id']
                    if headroom(fac, tid) < 1:
                        continue
                    cost = cost_at(t, sc_by_tid[fac][tid])
                    if spend[fac] + cost <= budget:
                        add_unit(fac, tid, t, 1)
                    break

    # ---- Phase 5: exact-fill the remainder ----
    # Every unit cost is even except Submarine (9) when use_sc is False (no
    # SC cost variants to supply other odd denominations), so an odd
    # remainder can only be closed exactly by a Submarine purchase/removal.
    # Rather than force that (which can overspend a tight budget, or strip
    # a faction's only Submarine below the diversity floor), a small
    # leftover is accepted: get as close to 0 as the available
    # denominations allow and stop.
    for fac in FACTION_ORDER:
        remainder = budget - spend[fac]
        territories_by_cap = sorted(profile[fac], key=lambda r: -cap_of(r))
        non_naval_used = [t for t in used_types[fac] if t not in NAVAL_TYPES]
        guard = 0
        while remainder >= 3 and guard < 500:
            guard += 1
            placed = False
            for t in sorted(non_naval_used, key=lambda t: cost_at(t, False)):
                for r in territories_by_cap:
                    tid = r['id']
                    if headroom(fac, tid) < 1:
                        continue
                    cost = cost_at(t, sc_by_tid[fac][tid])
                    if cost <= remainder:
                        add_unit(fac, tid, t, 1)
                        remainder -= cost
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                break

        # leftover-budget rule: upgrade an existing Infantry -> Mechanized
        # Infantry to soak up a residual remainder (Phase 1 guarantees
        # Infantry exists at every garrisoned territory, so an upgrade
        # target always exists there).
        guard = 0
        while remainder > 0 and guard < 50:
            guard += 1
            upgraded = False
            for r in sorted(profile[fac], key=lambda r: r['sc'], reverse=True):
                tid = r['id']
                is_sc = r['sc']
                delta = cost_at('Mechanized Infantry', is_sc) - cost_at('Infantry', is_sc)
                if delta <= remainder and purchases[fac].get(tid, {}).get('Infantry', 0) >= 1:
                    purchases[fac][tid]['Infantry'] -= 1
                    if purchases[fac][tid]['Infantry'] == 0:
                        del purchases[fac][tid]['Infantry']
                    purchases[fac][tid]['Mechanized Infantry'] = purchases[fac][tid].get('Mechanized Infantry', 0) + 1
                    spend[fac] += delta
                    remainder -= delta
                    upgraded = True
                    break
            if not upgraded:
                break
        # a remainder of 2 (Infantry -> Mechanized Infantry costs 3, so it cannot close it): upgrade a
        # Mechanized Infantry to Armor instead (+2 at any price)
        if remainder >= 2:
            for r in sorted(profile[fac], key=lambda r: r['sc'], reverse=True):
                tid = r['id']
                delta = cost_at('Armor', r['sc']) - cost_at('Mechanized Infantry', r['sc'])
                if delta <= remainder and purchases[fac].get(tid, {}).get('Mechanized Infantry', 0) >= 1:
                    purchases[fac][tid]['Mechanized Infantry'] -= 1
                    if purchases[fac][tid]['Mechanized Infantry'] == 0:
                        del purchases[fac][tid]['Mechanized Infantry']
                    purchases[fac][tid]['Armor'] = purchases[fac][tid].get('Armor', 0) + 1
                    spend[fac] += delta
                    remainder -= delta
                    break
        # SC-swap: a unit bought at a Strategic Center costs its sc_cost, the same
        # unit bought anywhere else its full cost -- so when the remainder equals
        # that difference, moving one land unit from an SC to a non-SC territory
        # (with room, leaving the SC at least one land unit) closes it exactly.
        if use_sc and remainder > 0:
            done = False
            for t in [x for x in LAND_TYPES if x in used_types[fac]]:
                if PURCHASABLE[t]['cost'] - PURCHASABLE[t]['sc_cost'] != remainder:
                    continue
                for src in profile[fac]:
                    if not src['sc'] or purchases[fac].get(src['id'], {}).get(t, 0) < 1:
                        continue
                    land_here = sum(q for u, q in purchases[fac][src['id']].items() if u in LAND_TYPES)
                    if land_here < 2:
                        continue
                    for dst in sorted(profile[fac], key=lambda r: -cap_of(r)):
                        if dst['sc'] or headroom(fac, dst['id']) < 1 or cap_of(dst) < 1:
                            continue
                        purchases[fac][src['id']][t] -= 1
                        if purchases[fac][src['id']][t] == 0:
                            del purchases[fac][src['id']][t]
                        cap_used[fac][src['id']] -= 1
                        spend[fac] -= cost_at(t, True)
                        add_unit(fac, dst['id'], t, 1)
                        remainder -= PURCHASABLE[t]['cost'] - PURCHASABLE[t]['sc_cost']
                        done = True
                        break
                    if done:
                        break
                if done:
                    break
        if remainder > 2:
            print(f'WARNING: {fac} has {remainder} IPC unspent -- larger than expected, worth a look')
        elif remainder != 0:
            print(f'note: {fac} has {remainder} IPC unspent (small change, not a whole unit)')

    # ---- naval deploy overrides (only for naval territories whose default zone collided) ----
    naval_overrides = {}
    for fac in FACTION_ORDER:
        for nt in naval_territories[fac]:
            if not nt['override']:
                continue
            units_here = purchases[fac].get(nt['id'], {})
            naval_here = {u: nt['zone'] for u in units_here if u in NAVAL_TYPES}
            if naval_here:
                naval_overrides.setdefault(fac, {})[str(nt['id'])] = naval_here

    # ---- promotions: the N unit types with the faction's highest doctrine
    # weight, one pick each, at the lowest-id territory that has that type ----
    promotions = {}
    for fac in FACTION_ORDER:
        if promotions_count <= 0:
            promotions[fac] = []
            continue
        first_tid_of = {}
        for tid, unit_qty in sorted(purchases[fac].items()):
            for unit, qty in unit_qty.items():
                if qty > 0 and unit not in first_tid_of:
                    first_tid_of[unit] = tid
        types_by_doctrine = sorted(first_tid_of, key=lambda u: -weight_of[fac].get(u, 0))
        promotions[fac] = [{'territory_id': first_tid_of[u], 'unit': u} for u in types_by_doctrine[:promotions_count]]

    # ---- assemble scenario JSON ----
    scenario = {
        "_comment": comment,
        "budget_ipc": budget,
        "purchases": {
            fac: [
                {"territory_id": tid, "units": [{"unit": u, "qty": q} for u, q in sorted(unit_qty.items())]}
                for tid, unit_qty in sorted(purchases[fac].items())
            ]
            for fac in FACTION_ORDER
        },
        "promotions": promotions,
        "carrier_escorts": carrier_escorts,
        "naval_deploy_overrides": naval_overrides,
    }

    json.dump(scenario, open(out_path, 'w'), indent=2)

    print('wrote', out_path)
    for fac in FACTION_ORDER:
        types = sorted({u for uq in purchases[fac].values() for u, q in uq.items() if q > 0})
        navy_desc = ', '.join(f'{nt["name"]}->{nt["zone"]}{"*" if nt["override"] else ""}' for nt in naval_territories[fac])
        print(f'  {fac}: spend={spend[fac]} types({len(types)})={types}')
        print(f'         naval: {navy_desc}')


if __name__ == '__main__':
    print('The canonical 125-MPC scenario is made by tools/generate_scenario_weighted.py; '
          'the 100-MPC one by tools/generate_scenario_100ipc.py.')
