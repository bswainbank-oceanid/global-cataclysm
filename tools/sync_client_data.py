"""
Writes the read-only reference data the Godot client needs into client/data and
client/assets, since a Godot project can only load files from inside its own res://
tree. Both target dirs are gitignored -- this is a regenerable build step: nobody
hand-edits the copies.

The data comes from the scenario's modules (docs/DATA_MODEL.md), resolved into the
files the client reads:
  territories.json       map size and topology, every location (name, land/sea, anchor,
                         and for land: faction, value, Strategic Center)
  territory_shapes.json  each location's boundary polygons
  factions.json          faction name, color, icon, in faction-set order
  units.json             unit types with stats, abilities, icon, display and battle order
plus the map image (as assets/base_map.png) and the unit icons.

Run: python tools/sync_client_data.py [--scenario GC72_Scenario]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / 'client'
sys.path.insert(0, str(ROOT))

from engine.game_config import GameConfig  # noqa: E402
from engine.repository import DEFAULT_SCENARIO_ID  # noqa: E402


def client_files(config):
    """{file name: document} for client/data."""
    info = config.map_info()
    territories = {
        'scenario': config.scenario_id,
        'reference_image_width_px': info['width_px'],
        'reference_image_height_px': info['height_px'],
        'wraps_east_west': info['wraps_east_west'],
        'spaces': list(config.territories().values()),
    }
    shapes = {
        'reference_image_width_px': info['width_px'],
        'shapes': {str(tid): polys for tid, polys in config.boundaries().items()},
    }
    factions = {'factions': config.factions()}
    units = {
        'category_icons': config.unit_set.get('category_icons', {}),
        'units': {t: {k: v for k, v in d.items()} for t, d in config.units().items()},
    }
    return {'territories.json': territories, 'territory_shapes.json': shapes,
            'factions.json': factions, 'units.json': units}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scenario', default=DEFAULT_SCENARIO_ID)
    args = ap.parse_args()
    config = GameConfig(args.scenario)

    data_dir = CLIENT / 'data'
    icon_dir = CLIENT / 'assets' / 'icons'
    data_dir.mkdir(parents=True, exist_ok=True)
    icon_dir.mkdir(parents=True, exist_ok=True)
    for stale in ('adjacency.json',):  # no longer synced
        (data_dir / stale).unlink(missing_ok=True)
    files = client_files(config)
    for name, doc in files.items():
        with open(data_dir / name, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write('\n')
    shutil.copy2(ROOT / config.map_info()['image'], CLIENT / 'assets' / 'base_map.png')
    icons = {d['icon'] for d in config.units().values() if d.get('icon')}
    icons |= {f['icon'] for f in config.factions().values() if f.get('icon')}
    for icon in sorted(icons):
        shutil.copy2(ROOT / 'assets' / 'icons' / icon, icon_dir / icon)
    print(f'synced {config.scenario_id}: {len(files)} data files, the map image and {len(icons)} icons into {CLIENT}')


if __name__ == '__main__':
    main()
