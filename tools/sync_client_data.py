"""
Copies the read-only reference data the Godot client needs (territories,
polygon shapes, factions, units, adjacency, base map, unit icons) into
client/data and client/assets, since a Godot project can only load files
from inside its own res:// tree. Both target dirs are gitignored -- this
is a regenerable build step, same philosophy as the rest of the data
pipeline (docs/PIPELINE.md): nobody hand-edits the copies.

Run: python tools/sync_client_data.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / 'client'

DATA_FILES = ['territories.json', 'territory_shapes.json', 'factions.json', 'units.json', 'adjacency.json']


def main():
    (CLIENT / 'data').mkdir(parents=True, exist_ok=True)
    (CLIENT / 'assets' / 'icons').mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copy2(ROOT / 'data' / name, CLIENT / 'data' / name)
    shutil.copy2(ROOT / 'assets' / 'base_map.png', CLIENT / 'assets' / 'base_map.png')
    for icon in (ROOT / 'assets' / 'icons').glob('*.svg'):
        shutil.copy2(icon, CLIENT / 'assets' / 'icons' / icon.name)
    print(f'synced {len(DATA_FILES)} data files, base map, and icons into {CLIENT}')


if __name__ == '__main__':
    main()
