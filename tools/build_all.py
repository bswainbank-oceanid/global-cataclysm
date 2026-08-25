"""
Run the full regeneration pipeline, in dependency order, producing:
  exports/GC1972_Territories.xlsx
  exports/map.png

Everything under derived/ and exports/ is fully regenerable from data/ +
tools/ -- this script is the one place that order is written down.

tools/export_adjacency.py is NOT run here: it's a one-time migration from
the original graph.pkl pickle (which lives outside this repo) into
data/adjacency.json, which is now the canonical, checked-in source. Re-run
it manually only if the underlying adjacency graph itself changes.

Run from the repo root:
    python3 tools/build_all.py
"""
import subprocess
import sys

STEPS = [
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
