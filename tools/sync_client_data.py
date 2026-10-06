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
  factions.json          faction name, color, icon, in faction-set order; the Neutral colour/faction
  units.json             unit types with stats, abilities, icon, display and battle order
  build.json             the client's build number (server/build.py), shown on the main menu
  history.json           the game's back story, for the History screen (data/history.json, written by
                         tools/import_history.py)
plus the map image (as assets/base_map.png), the unit and faction icons, the unit sounds (assets/sounds),
the fonts (assets/fonts) and the logo artwork (assets/logo, renamed by LOGO_FILES).

Run: python tools/sync_client_data.py [--scenario GC72_Scenario]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# assets/logo file -> its name in client/assets/logo
LOGO_FILES = {
    '01_global-cataclysm-splash-1920x1080.png': 'splash.png',
    'Global Cataclysm 1972 - start 1024x1024.png': 'start.png',
    'Vintage Global Defense Poster Icon 512x512.png': 'icon_512.png',
    'Vintage Global Defense Poster Icon 256x256.png': 'icon_256.png',
    'Vintage Global Defense Poster Icon 64x64.png': 'icon_64.png',
    'Vintage Global Defense Poster Icon 32x32.png': 'icon_32.png',
    'global-cataclysm-mark.svg': 'mark.svg',
}
CLIENT = ROOT / 'client'
sys.path.insert(0, str(ROOT))

from engine.game_config import GameConfig  # noqa: E402
from engine.repository import DEFAULT_SCENARIO_ID  # noqa: E402
from server.build import build_info  # noqa: E402


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
    factions = {'factions': config.factions(), 'neutral': config.neutral_faction(),
                'noncombatant': config.noncombatant_faction()}
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
    shutil.copy2(ROOT / 'data' / 'history.json', data_dir / 'history.json')  # (tools/import_history.py)
    with open(data_dir / 'build.json', 'w', encoding='utf-8') as f:  # the client's build number (server/build.py)
        json.dump(build_info(refresh=True), f)
    shutil.copy2(ROOT / config.map_info()['image'], CLIENT / 'assets' / 'base_map.png')
    icons = {d['icon'] for d in config.units().values() if d.get('icon')}
    icons |= {f['icon'] for f in config.factions().values() if f.get('icon')}
    icons |= {f['icon'] for f in (config.neutral_faction(), config.noncombatant_faction()) if f.get('icon')}
    for icon in sorted(icons):
        (icon_dir / icon).parent.mkdir(parents=True, exist_ok=True)  # (faction icons sit in a subfolder)
        shutil.copy2(ROOT / 'assets' / 'icons' / icon, icon_dir / icon)
    # the unit sounds each unit type names (assets/sounds: tools/make_sounds.py)
    sounds = {d[k] for d in config.units().values() for k in ('move_sound', 'attack_sound') if d.get(k)}
    for sound in sorted(sounds):
        dest = CLIENT / 'assets' / 'sounds' / sound
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'assets' / 'sounds' / sound, dest)
    # the style guide's fonts (assets/fonts) and the logo artwork (assets/logo), under the names the client loads
    font_dir = CLIENT / 'assets' / 'fonts'
    font_dir.mkdir(parents=True, exist_ok=True)
    for font in sorted((ROOT / 'assets' / 'fonts').glob('*.ttf')):
        shutil.copy2(font, font_dir / font.name)
    logo_dir = CLIENT / 'assets' / 'logo'
    logo_dir.mkdir(parents=True, exist_ok=True)
    for src, dest in LOGO_FILES.items():
        shutil.copy2(ROOT / 'assets' / 'logo' / src, logo_dir / dest)
    print(f'synced {config.scenario_id}: {len(files)} data files, the map image, {len(icons)} icons, the fonts and the logo '
          f'into {CLIENT}')


if __name__ == '__main__':
    main()
