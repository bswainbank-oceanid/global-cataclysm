"""
MPC (the game's currency) math that isn't naturally part of state.py's
plain data model or movement.py's reachability queries. Pure functions
over a GameState snapshot -- same convention as movement.py: read-only,
no mutation, no side effects.
"""


def compute_income(faction, game_state, data_module):
    """MPC income for `faction`: the sum of (value + 2 if
    strategic_center) over every LAND territory it currently controls
    that ISN'T contested -- see data/rules.json's production.
    income_formula. A contested territory contributes to neither side
    (combat.contested_territory_rule); sea zones carry no value field
    and are never an income source. Used both to seed each faction's
    starting treasury_mpc at setup (engine/setup.py) and, once engine.py
    exists, by the Deploy + Income phase every turn."""
    terrs = data_module.territories()
    total = 0
    for tid, t in game_state.territories.items():
        if t.owner != faction or t.contested_by:
            continue
        terr = terrs[tid]
        if terr['type'] != 'land':
            continue
        total += terr['value'] + (2 if terr.get('strategic_center') else 0)
    return total
