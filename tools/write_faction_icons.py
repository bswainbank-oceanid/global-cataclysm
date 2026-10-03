"""
Writes the faction icon SVGs (tools/faction_icons.py) into assets/icons/factions/ -- each faction's file is
named by its `icon` in the faction set (a path under assets/icons) -- and, with --set-faction-set, fills in
those `icon` fields: <faction id lowercased>.svg, and neutral.svg for the built-in Neutral and Noncombatant.

    python tools/write_faction_icons.py [--set-faction-set] [--scenario GC72_Scenario]
"""
import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faction_icons  # noqa: E402
import tool_data  # noqa: E402

DIR = 'factions'  # under assets/icons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
    ap.add_argument('--set-faction-set', action='store_true')
    args = ap.parse_args()
    tool_data.use_scenario(args.scenario)
    out_dir = tool_data.root_path(os.path.join('assets', 'icons', DIR))
    os.makedirs(out_dir, exist_ok=True)
    for name in faction_icons.ICONS:
        with open(os.path.join(out_dir, f'{name}.svg'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(faction_icons.svg(name))
    print(f'wrote {len(faction_icons.ICONS)} icons into {out_dir}')
    if args.set_faction_set:
        config = tool_data.config()
        repo = config.repo
        fset = copy.deepcopy(config.faction_set)
        for f in fset['factions']:
            name = f['id'].lower()
            f['icon'] = f'{DIR}/{name}.svg' if name in faction_icons.ICONS else f'{DIR}/{faction_icons.DEFAULT_ICON}.svg'
        for key in ('neutral', 'noncombatant'):
            if key in fset:
                fset[key]['icon'] = f'{DIR}/{faction_icons.DEFAULT_ICON}.svg'
        repo.save(fset)
        print('set the icons of faction set', fset['id'])


if __name__ == '__main__':
    main()
