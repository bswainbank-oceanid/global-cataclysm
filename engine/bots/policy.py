"""
Policy constraints shared by every bot in this package -- rules a bot
must never violate regardless of its own decision strategy, kept here
once so a future bot doesn't have to remember to reimplement them.
"""


def excluded_naval_purchase_zones(data_module):
    """Sea zone territory_ids no bot may ever purchase a unit at.
    Reuses setup.excluded_naval_zones (data/rules.json) -- the same
    zones that never receive a naval deployment in any starting scenario
    (e.g. the landlocked Caspian Sea, id 43, per
    setup.excluded_naval_zones_note). The underlying reason (no real
    naval access) is permanent, not setup-only, so a bot purchasing
    there during ordinary play would be just as wrong -- this is a bot
    policy, not an engine-level restriction: GameEngine itself still
    allows it (a human player might have some reason the bots don't
    need to model)."""
    return set(data_module.rules()['setup']['excluded_naval_zones'])
