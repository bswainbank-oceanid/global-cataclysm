"""
Run the full regeneration pipeline, in dependency order, producing:
  exports/GC1972_Territories.xlsx
  exports/map.png

Everything under derived/ and exports/ is fully regenerable from data/ +
tools/ -- this script is the one place that order is written down.
data/adjacency.json is also regenerated here (tools/compute_adjacency.py):
it's kept in data/ rather than derived/ since it's foundational/canonical
enough to want reviewed directly, but nothing in it is hand-edited.

Run from the repo root:
    python3 tools/build_all.py
"""
import subprocess
import sys

STEPS = [
    ['python3', 'tools/compute_adjacency.py'],
    ['python3', 'tools/compute_foreign_neighbors.py'],
    ['python3', 'tools/compute_faction_profile.py'],
    ['python3', 'tools/build_master_xlsx.py'],
    ['python3', 'tools/build_setup_tab.py'],
    ['python3', '/mnt/skills/public/xlsx/scripts/recalc.py', 'exports/GC1972_Territories.xlsx'],
    ['python3', 'tools/validate_setup.py'],
    ['python3', 'tools/render_map.py'],
]

for step in STEPS:
    print('>>>', ' '.join(step))
    result = subprocess.run(step)
    if result.returncode != 0:
        print(f'FAILED: {" ".join(step)} (exit {result.returncode})')
        sys.exit(result.returncode)

print()
print('Pipeline complete: exports/GC1972_Territories.xlsx, exports/map.png')
