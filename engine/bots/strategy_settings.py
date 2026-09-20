"""
The strategy bots' settings and their weighted random draws.

Everything a strategy bot chooses by odds -- its strategy style, the order of its secondary objectives,
each unit it buys -- is a weighted random draw over weights taken from data/bot_settings.json (built
from reference/GC Bot Settings.ods by tools/build_bot_settings.py). A weight of 0 is never drawn.
"""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'bot_settings.json')

STYLES = ('Strategic', 'Defensive', 'Expansive', 'Controlling')  # the concrete ones; 'Variable' re-rolls among them
ALL_STYLES = STYLES + ('Variable',)

# The secondary objectives, in the sheet's own names (the threshold rows are keyed by these).
SECONDARY = ('expand_territory', 'hold_frontier', 'control_oceans', 'pursue_sc_1', 'pursue_sc_2', 'pursue_sc_3')

_cache = None


class StrategySettings:
    def __init__(self, raw):
        self.unit_weights = raw['unit_weights']          # faction -> unit type -> weight
        self.strategy_weights = raw['strategy_weights']  # faction -> style -> weight
        self.thresholds = raw['thresholds']              # style -> objective -> {min, max[, weight]}
        self.distance_costs = raw['distance_costs']      # friendly / sea / enemy / allied -> cost per square

    # -- the draws ---------------------------------------------------------------------------

    def draw_style(self, faction, rng):
        """The style a faction plays this game (or, for a Variable bot, its 'Variable' -- see turn_style)."""
        weights = self.strategy_weights[faction]
        return weighted_choice(rng, list(ALL_STYLES), [weights.get(s, 0) for s in ALL_STYLES])

    def turn_style(self, faction, base_style, rng):
        """The concrete style playing this turn: the base style itself, or -- for Variable -- a fresh draw
        among the four concrete styles by the faction's own weights."""
        if base_style != 'Variable':
            return base_style
        weights = self.strategy_weights[faction]
        return weighted_choice(rng, list(STYLES), [weights.get(s, 0) for s in STYLES])

    def secondary_order(self, style, rng):
        """This turn's order of consideration for the secondary objectives: weighted draws without
        replacement by the style's weights (a weight of 0 is never drawn, so that objective is skipped)."""
        weights = {name: self.thresholds[style][name]['weight'] for name in SECONDARY}
        return weighted_order(rng, list(SECONDARY), [weights[n] for n in SECONDARY])

    def unit_odds(self, faction, unit_types):
        """Purchase odds for the given unit types (a weight per type; unknown types weigh nothing)."""
        weights = self.unit_weights.get(faction, {})
        return [weights.get(t, 0) for t in unit_types]

    def limits(self, style, objective):
        """(min, max) success probability, as fractions, for an objective under a style."""
        row = self.thresholds[style][objective]
        return row['min'] / 100.0, row['max'] / 100.0


def weighted_choice(rng, items, weights):
    """One item, by odds. Items with weight 0 are never picked; None if every weight is 0."""
    total = sum(w for w in weights if w > 0)
    if total <= 0:
        return None
    r = rng.random() * total
    acc = 0.0
    for item, w in zip(items, weights):
        if w <= 0:
            continue
        acc += w
        if r < acc:
            return item
    return next(i for i, w in reversed(list(zip(items, weights))) if w > 0)


def weighted_order(rng, items, weights):
    """All the items with a positive weight, in an order drawn by odds without replacement."""
    pool = [(i, w) for i, w in zip(items, weights) if w > 0]
    out = []
    while pool:
        pick = weighted_choice(rng, [i for i, _ in pool], [w for _, w in pool])
        out.append(pick)
        pool = [(i, w) for i, w in pool if i != pick]
    return out


def load_settings(path=None):
    global _cache
    if path is None and _cache is not None:
        return _cache
    with open(path or _PATH, encoding='utf-8') as f:
        settings = StrategySettings(json.load(f))
    if path is None:
        _cache = settings
    return settings
