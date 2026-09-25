"""
Run the full regeneration pipeline, in dependency order, for a scenario's modules
(docs/DATA_MODEL.md, docs/PIPELINE.md):

  1. tools/extract_territory_shapes.py  map image -> the Map's boundary polygons
  2. tools/compute_adjacency.py         boundaries + overrides -> the Map's adjacency
  3. tools/compute_foreign_neighbors.py -> derived/adjacency_foreign.json
  4. tools/compute_faction_profile.py   -> derived/faction_territory_profile.json
  5. tools/validate_modules.py          every module's fields and references
  6. tools/validate_setup.py            the standard starting setup against the setup rules
  7. tools/export_sheets.py             module JSON -> sheets/*.ods, so the sheets show what the game reads
  8. tools/render_map.py                -> exports/map.png

Everything under derived/ and exports/ is regenerable from data/modules/ + tools/; the
Map's boundaries and adjacency are regenerated in place (nothing in them is hand-edited
but the adjacency overrides). Stops at the first failing step.

Run from the repo root:
    python tools/build_all.py [--scenario GC72_Scenario]
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--scenario', default='GC72_Scenario')
args = parser.parse_args()
scenario = ['--scenario', args.scenario]

STEPS = [
    ['tools/extract_territory_shapes.py'] + scenario,  # first: adjacency is derived from the outlines
    ['tools/compute_adjacency.py'] + scenario,
    ['tools/compute_foreign_neighbors.py'] + scenario,
    ['tools/compute_faction_profile.py'] + scenario,
    ['tools/validate_modules.py'],
    ['tools/validate_setup.py', '--setup', 'standard'] + scenario,
    ['tools/export_sheets.py'],
    ['tools/render_map.py'] + scenario,
]

os.makedirs(os.path.join(ROOT, 'derived'), exist_ok=True)
os.makedirs(os.path.join(ROOT, 'exports'), exist_ok=True)
for step in STEPS:
    print('>>>', ' '.join(step))
    result = subprocess.run([sys.executable] + step, cwd=ROOT)
    if result.returncode != 0:
        print(f'FAILED: {" ".join(step)} (exit {result.returncode})')
        sys.exit(result.returncode)

print()
print('Pipeline complete: data/modules (map boundaries, adjacency), derived/, sheets/, exports/map.png')
