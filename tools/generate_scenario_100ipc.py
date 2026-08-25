"""
Generate data/scenarios/starting_setup_100ipc.json: a smaller, faster-to-
set-up alternative scenario. Same algorithm and doctrine logic as
tools/generate_scenario.py (see that module's docstring), with a
different ruleset:
  - 100 IPC per faction instead of 200
  - Strategic Centers are ignored entirely -- no cost discount, and the
    stacking cap is a flat value+2 (no +2 SC bonus stacked on top)
  - baseline garrison covers only foreign-bordering territories, not
    every owned territory (100 IPC doesn't stretch to garrison all of
    UER's 19 territories the way 200 IPC covers all of them)
  - 0 promotions instead of 3
  - >=5 purchased unit types per faction instead of >=6

Every other setup rule from data/rules.json (coastal-only naval purchase,
no-shared-sea-zone, carrier-must-have-escort, land-unit-at-foreign-border,
leftover-budget-rule) still applies -- see tools/validate_setup.py, called
with --no-sc --budget 100 --min-types 5 to check this file instead of the
canonical one.

Run from the repo root, after derived/faction_territory_profile.json has
been (re)built:
    python3 tools/generate_scenario_100ipc.py
"""
from generate_scenario import generate_scenario

generate_scenario(
    budget=100,
    use_sc=False,
    min_types=5,
    promotions_count=0,
    garrison_all_territories=False,
    out_path='data/scenarios/starting_setup_100ipc.json',
    comment=("Starting-setup scenario: 100 IPC per faction, no Strategic Centers (flat "
             "value+2 stacking cap, no cost discount), no promotions, territory ownership "
             "from data/territories.json. purchases is per-territory unit buys; "
             "carrier_escorts/naval_overrides are the fixed setup-time picks layered on top."),
)
