"""
Writes the fallback faction icon SVGs (tools/faction_icons.py: neutral.svg) into assets/icons/faction/ -- never
over a file already there (the factions' own artwork lives there too) -- and, with --set-faction-set, fills in
the faction set's `icon` fields: faction/<faction id lowercased>.svg where that file exists, else the fallback;
neutral.svg for the built-in Neutral and Noncombatant.

    python tools/write_faction_icons.py [--set-faction-set] [--scenario GC72_Scenario]
"""
import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faction_icons  # noqa: E402
import tool_data  # noqa: E402

DIR = 'faction2'  # under assets/icons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scenario', default=tool_data.DEFAULT_SCENARIO_ID)
    ap.add_argument('--set-faction-set', action='store_true')
    args = ap.parse_args()
    tool_data.use_scenario(args.scenario)
    out_dir = tool_data.root_path(os.path.join('assets', 'icons', DIR))
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in faction_icons.ICONS:
        target = os.path.join(out_dir, f'{name}.svg')
        if os.path.exists(target):
            continue  # never over artwork, or a hand-edited file
        with open(target, 'w', encoding='utf-8', newline='\n') as f:
            f.write(faction_icons.svg(name))
        written.append(name)
    print(f'wrote {", ".join(written) or "nothing (every icon file is already there)"} into {out_dir}')
    if args.set_faction_set:
        config = tool_data.config()
        repo = config.repo
        fset = copy.deepcopy(config.faction_set)
        for f in fset['factions']:
            name = f['id'].lower()
            own = os.path.exists(os.path.join(out_dir, f'{name}.svg'))
            f['icon'] = f'{DIR}/{name}.svg' if own else f'{DIR}/{faction_icons.DEFAULT_ICON}.svg'
        for key in ('neutral', 'noncombatant'):
            if key in fset:
                fset[key]['icon'] = f'{DIR}/{faction_icons.DEFAULT_ICON}.svg'
        repo.save(fset)
        print('set the icons of faction set', fset['id'])


if __name__ == '__main__':
    main()
