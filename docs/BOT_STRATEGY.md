# Strategy bots

The heuristic bot (`engine/bots/strategy_bot.py`, planner in `engine/bots/planner.py`) follows
`reference/GC Bot Strategy.rtf` with the numbers in `reference/GC Bot Settings.ods`
(built into `data/bot_settings.json` by `tools/build_bot_settings.py`). The random bot
(`RandomBot`) stays as the baseline; a bot seat picks one in the launch screen ("Bot AI") or in the
`new_game` seat settings (`"ai": "strategy" | "random"`, default `strategy`).

## How a turn goes

* **Start of game:** each faction draws a strategy style (Strategic / Defensive / Expansive /
  Controlling / Variable) by its row of the Strategy Preferences sheet. Variable re-draws every turn among
  the four concrete styles by the same row.
* **Purchase:** the whole turn is planned (a `Planner` in `'full'` mode) and the purchases staged; the
  combat moves are remembered.
* **Combat Move:** the remembered combat moves are played.
* **Non-Combat Move:** after combat resolves, a `'noncombat'` plan is made from the board as it now stands
  (moves only; the money is already spent).
* **Alliances:** unchanged (`alliance_policy`, the alliance strategy/behavior settings).

Every choice by odds -- style, the order of the secondary objectives, each unit bought -- is a weighted random
draw from the bot's own seeded rng; a weight of 0 is never drawn.

## Objectives

Primary, in this order: **hold SCs**, **capture SCs**, **reinforce contested**, **punish betrayers** (territories
where `reclaim_bonus_for` is the bot), **fill defensive gaps**. A treacherous bot that has decided to withdraw then
does **treasonous capture** (non-combat moves into allied non-SC land; it uses the *expand territory* limits).
Secondary, in a weighted order drawn each turn from the style's weights: **expand territory**, **hold frontier**,
**control oceans**, **pursue SC 1 / 2 / 3**. Last, whatever is left goes to **pursuing SCs** (below).

Each objective rates its chance of success with the fast simulator (`bots/battle_sim.py`: 200 samples, the
battle run without a round limit, stopping early when the answer is clear) and applies its style's limits:
if everything that could be committed leaves the chance under the objective's **min**, it is not pursued (nothing
is committed); otherwise resources are added -- units already there, then purchases (by the faction's unit odds),
then units that can move in, cheapest first -- until the chance passes the objective's **max**.

Exceptions to "nothing is committed below the min", chosen so money is not left idle:

* **hold frontier** keeps the units and purchases it committed even when it cannot reach the min;
* **expand territory** and **control oceans** buy units toward a target they could not yet beat (at the purchase
  spots nearest it) and keep them;
* **pursue SC** keeps what it bought toward a target even when the force is not yet strong enough to advance.

## Spending: Strategic Centers are favoured

`Planner.buy_toward` picks the purchase spot near a target; a spot that is (or is paid for by) a Strategic Center
counts `SC_BIAS` (8 path-cost points) closer than it is: a unit bought there costs the SC price and there is room for
two more, and it garrisons where it matters.

## Chasing Strategic Centers, overkill welcome

The enemy SCs are ranked by *challenge number* (the cheapest path from one of the bot's SCs, squares costing 2 friendly / 3 sea /
5 enemy / 3 allied, plus the value of the enemy units within two spaces of the target and of every path square; sea
units only where they are on the path). Objectives *pursue SC 1 / 2 / 3* send every free unit for which that target is the
nearest of this turn's targets, attacking enemies on the way when the odds are right, and buy units until the force could
take the target. After all the objectives, `objective_pursue_leftovers` sends every remaining unit at the nearest of the three
targets and spends all the money still left on units bought toward them (SC spots first). Only the last defender of a
territory an enemy land unit could walk into stays behind.

## Effort budget and reproducibility

Planning effort is counted in simulated battles per pass (`DEFAULT_BUDGET` 3500, roughly 1-3 seconds), not seconds, so
a seeded game replays exactly (`server/tests/test_reproducible.py`). Each objective may spend a share of the budget
(hold SCs 35%, capture 15%, ...; unspent budget carries on). A one-off `dev.bot_budget` in the `new_game` settings
changes it.

## Measuring

`tools/bot_arena.py` plays engine-only games between the AIs:

    python tools/bot_arena.py --games 12 --mix strategy=1,random=5 --budget 1200 --seed 100
    python tools/bot_arena.py --games 8 --mix strategy=3,random=3

It reports, per AI, how many seats survived (were not eliminated when the game ended) and the average Strategic
Centers, territories and units at the end. `engine/tests/test_battle_sim.py` checks the fast simulator against the real
resolver.

## Interpretations to confirm

* Adding units cheapest-first (movers); purchases by unit odds.
* Hold SC's min: if even everything cannot reach it, the SC is given up (nothing committed).
* Capturing needs a land unit in the attack; aircraft alone can only fight.
* The enemy for risk purposes is the strongest one or two hostile factions that can reach the target (they act one at a time).
* Purchased units count as defenders (they deploy before the enemy moves) and, for the contested-territory and build-up
  estimates, as attackers.
