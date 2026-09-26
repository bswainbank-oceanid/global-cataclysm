"""
The strategy bots' settings and their weighted random draws.

Everything a strategy bot chooses by odds -- its strategy style, the order of its secondary objectives,
each unit it buys -- is a weighted random draw over weights taken from the scenario's bot modules
(FactionWeightSet, StrategyThresholdSet, Objectives, PrimaryObjectiveOrder -- docs/DATA_MODEL.md). A weight
of 0 is never drawn.
"""
from .. import data as _default_data


class StrategySettings:
    def __init__(self, raw):
        self.unit_weights = raw['unit_weights']          # faction -> unit type -> weight
        self.strategy_weights = raw['strategy_weights']  # faction -> style -> weight
        self.thresholds = raw['thresholds']              # style -> objective -> {min, max[, weight]}
        self.distance_costs = raw['distance_costs']      # friendly / sea / enemy / allied -> cost per square
        # The concrete styles, in the module's order; a variable style re-draws among them every turn.
        self.styles = tuple(self.thresholds)
        self.variable_styles = tuple(raw.get('variable_strategies', ()))
        self.all_styles = self.styles + self.variable_styles
        # The secondary objectives, in the module's order (the threshold rows are keyed by these).
        self.secondary = tuple(o['id'] for o in raw.get('objectives', []) if o['kind'] == 'secondary')
        self.primary_order = raw.get('primary_order', [])
        self.secondary_budget_share = raw.get('secondary_budget_share')
        self.final_order = raw.get('final_order', [])

    # -- the draws ---------------------------------------------------------------------------

    def draw_style(self, faction, rng):
        """The style a faction plays this game (or, for a Variable bot, its 'Variable' -- see turn_style)."""
        weights = self.strategy_weights[faction]
        return weighted_choice(rng, list(self.all_styles), [weights.get(s, 0) for s in self.all_styles])

    def turn_style(self, faction, base_style, rng):
        """The concrete style playing this turn: the base style itself, or -- for Variable -- a fresh draw
        among the four concrete styles by the faction's own weights."""
        if base_style not in self.variable_styles:
            return base_style
        weights = self.strategy_weights[faction]
        return weighted_choice(rng, list(self.styles), [weights.get(s, 0) for s in self.styles])

    def secondary_order(self, style, rng):
        """This turn's order of consideration for the secondary objectives: weighted draws without
        replacement by the style's weights (a weight of 0 is never drawn, so that objective is skipped)."""
        weights = {name: self.thresholds[style][name]['weight'] for name in self.secondary}
        return weighted_order(rng, list(self.secondary), [weights[n] for n in self.secondary])

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


def load_settings(data_module=None):
    """The StrategySettings for `data_module`'s scenario (the default scenario if it has no bot settings)."""
    global _cache
    source = data_module if data_module is not None and hasattr(data_module, 'bot_settings') else _default_data
    raw = source.bot_settings()
    if _cache is None or _cache[0] is not raw:
        _cache = (raw, StrategySettings(raw))
    return _cache[1]


_cache = None
