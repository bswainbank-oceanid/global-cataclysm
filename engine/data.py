"""
The reference data the engine needs -- units, rules, territories, adjacency,
factions -- for the scenario in use (GC72_Scenario unless use_scenario() picks
another). Everything comes from the scenario's modules (engine/game_config.py,
docs/DATA_MODEL.md); this module is the default "data module" the engine, bots
and server are handed, and any object with the same functions can stand in for
it (tests pass their own).

Read-only, built lazily on first access. Importing this module has no side
effects.
"""
from .game_config import GameConfig
from .repository import DEFAULT_SCENARIO_ID

_config = None


def config():
    """The GameConfig for the scenario in use."""
    global _config
    if _config is None:
        _config = GameConfig(DEFAULT_SCENARIO_ID)
    return _config


def use_scenario(scenario_id=DEFAULT_SCENARIO_ID, repository=None):
    """Switches every later lookup here to `scenario_id` (tools and tests)."""
    global _config
    _config = GameConfig(scenario_id, repository)
    return _config


def units():
    """{unit_type_id: {category, cost, sc_cost, attack_die, damage, defense, hp, combat_move,
    non_combat_move, purchasable, special_abilities, abilities, max_promotions, land_order, sea_order,
    display_order, icon, ...}}"""
    return config().units()


def rules():
    """The scenario's rule set."""
    return config().rules()


def territories():
    """{territory_id (int): {id, type, name, x, y, faction, value, strategic_center, ...}} in map order."""
    return config().territories()


def adjacency():
    """{territory_id (int): [neighbor territory_id, ...]}. Order carries no meaning -- treat as a
    set-like list."""
    return config().adjacency()


def factions():
    """{faction_code: {name, color, icon}} in the faction set's order."""
    return config().factions()


def sc_bonus():
    """What a Strategic Center adds to its territory's value (income and deploy cap)."""
    return config().sc_bonus()


def naval_deploy_excluded():
    """Sea zones that never host a naval deployment (landlocked)."""
    return config().naval_deploy_excluded()


def initial_setup(kind):
    """(InitialSetup, UnitPromotions) module documents for the 'standard' or 'defensive' setup."""
    return config().initial_setup(kind)


def bot_settings():
    """The strategy bots' weights, thresholds and planning order."""
    return config().bot_settings()
