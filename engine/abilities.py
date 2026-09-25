"""
The engine's ability library, as the rest of the engine asks about it: which unit
types have which special ability, and with what parameters (docs/DATA_MODEL.md). A
unit type's abilities come from its unit set (engine.data.units()[type]['abilities'],
{ability_id: params}); nothing in the engine names a unit type to decide what it can do.

Every function takes `unit_defs` (engine.data.units() or a test's stand-in) and a unit
type id.
"""

MUSTERING = 'mustering'                # may be purchased into a contested territory
DIG_IN = 'dig_in'                      # + defense_bonus while defending
HEROIC = 'heroic'                      # its own promotion cap (max_promotions)
AMPHIBIOUS = 'amphibious'              # may enter sea spaces as its transport_unit
BLITZ = 'blitz'                        # a combat move may pass through undefended enemy land
TRANSPORT = 'transport'                # the sea form of an amphibious unit
AIR_SUPERIORITY = 'air_superiority'    # its own die/damage in the air superiority round; triggers_round
INTERCEPTION = 'interception'          # enemy aircraft may not fly over a space it holds
CARRIER_AIR_WING = 'carrier_air_wing'  # carries air units
SUBMERGE = 'submerge'                  # cannot hit or be hit by air units
BOMBARDMENT = 'bombardment'            # may bombard adjacent land from the sea as its combat move
INDISCRIMINATE = 'indiscriminate'      # picks targets without the same-type preference


def of(unit_defs, unit_type):
    """{ability_id: params} for a unit type (empty for an unknown type)."""
    return unit_defs.get(unit_type, {}).get('abilities') or {}


def has(unit_defs, unit_type, ability):
    return ability in of(unit_defs, unit_type)


def param(unit_defs, unit_type, ability, name, default=None):
    return of(unit_defs, unit_type).get(ability, {}).get(name, default)


def unit_types_with(unit_defs, ability):
    """Every unit type with `ability`, in unit set order."""
    return [t for t in unit_defs if has(unit_defs, t, ability)]


def transport_unit(unit_defs, unit_type):
    """The unit type a land unit is while it is at sea: its amphibious ability's transport_unit, or
    (for a land unit that somehow ends up in a sea battle without one) the unit set's first unit type
    with the transport ability."""
    own = param(unit_defs, unit_type, AMPHIBIOUS, 'transport_unit')
    if own is not None:
        return own
    carriers = unit_types_with(unit_defs, TRANSPORT)
    return carriers[0] if carriers else None


def triggers_air_superiority(unit_defs, unit_type):
    return bool(param(unit_defs, unit_type, AIR_SUPERIORITY, 'triggers_round', False))
