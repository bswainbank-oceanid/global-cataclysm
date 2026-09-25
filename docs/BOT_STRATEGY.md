# Strategy bots

The heuristic bot (`engine/bots/strategy_bot.py`, planner in `engine/bots/planner.py`) follows
`reference/GC Bot Strategy.rtf`. Its numbers are the scenario's bot modules -- FactionWeightSet (unit
and strategy weights), StrategyThresholdSet (per-strategy objective risk limits and weights, distance
weights), Objectives and PrimaryObjectiveOrder (the planning order) -- edited in `sheets/` (see
`docs/DATA_MODEL.md`). The random bot
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
* **Diplomacy:** the alliance action is unchanged (`alliance_policy`, the alliance strategy/behavior settings). On top of it a bot forces surrenders (`RandomBot.plan_diplomacy_phase`): it asks a faction into an alliance first if that is legal (a bot never declines), never demands an ally's surrender, and, if it could win alone, forces everyone to surrender to end the game.

Every choice by odds -- style, the order of the secondary objectives, each unit bought -- is a weighted random
draw from the bot's own seeded rng; a weight of 0 is never drawn.

## Objectives

Primary, in this order: **hold SCs**, **capture SCs**, **reinforce contested**, **punish betrayers** (territories
where `reclaim_bonus_for` is the bot), **fill defensive gaps**. A treacherous bot that has decided to withdraw then
does **treasonous capture** (non-combat moves into allied non-SC land; it uses the *expand territory* limits).
Secondary, in a weighted order drawn each turn from the style's weights: **expand territory**, **hold frontier**,
**control oceans**, **pursue SC 1 / 2 / 3**, **empty land grab**. Last, whatever is left goes to **pursuing SCs** (below).

Each objective rates its chance of success with the fast simulator (`bots/battle_sim.py`: 200 samples, the
battle run without a round limit, stopping early when the answer is clear) and applies its style's limits:
if everything that could be committed leaves the chance under the objective's **min**, it is not pursued (nothing
is committed); otherwise resources are added -- units already there, then purchases (by the faction's unit odds),
then units that can move in, cheapest first -- until the chance passes the objective's **max**.

**Capturing Strategic Centers** measures the two limits differently: the **min** risk is the chance the attack at least
forces a *contest* -- the attackers hang on through the 3 rounds of a battle, or win outright -- estimated by the same
simulator run for 3 rounds (`battle_sim.estimate(..., max_rounds=3)`, `Planner.contest_odds`); the **max** is still the
chance of total victory, which is what the added resources aim at. So a bot will throw a force at an SC it cannot
quite beat if it can likely make it a contested territory. (Hold SC's max is lower than it was, so the bots defend their
Strategic Centers -- still always -- but not as vigorously.)

A faction that has been eliminated (surrendered) leaves its territory and Strategic Centers on the board, likely empty.
They stay in the set of Strategic Centers the bots try to capture (`Planner.capturable`), in the capture, pursue and
expand objectives alike.

**Control oceans** (secondary): finds enemy sea stacks within 3 hops of the bot's own land, biggest cost first, and
assaults each with whatever Sea/Air units could reach it (buying toward the ones it could not yet beat, same as
expand territory). A Cruiser left with nothing to show for that pass -- no stack in reach, or its stack had spares
left over once `assault` stopped adding units at the style's max-odds bar (`Planner._bombard_idle_cruisers`) --
looks instead for the highest-value occupied enemy land space among its own legal bombardment targets
(rules.json's `combat.cruiser_bombardment`) and bombards it: a free attack roll at the very start of Combat
Resolution, since the engine never actually moves it there and it takes no counter-fire. Skipped for a sea zone
threatened enough that its fleet would rather retreat (the same check the next step, below, uses) -- bombarding
still claims the unit's whole turn, same as any other combat move. Any idle Submarine/Aircraft Carrier sharing
that Cruiser's sea zone escorts it along for free rather than sitting out the turn. Last, any fleet a stronger
enemy could destroy withdraws to safety (an adjacent friendly-land sea zone, preferring one beside a Strategic
Center) rather than standing and losing everything.

**Empty land grab** (secondary; its weight in the settings sheet): finds enemy or eliminated-faction land nobody defends
or contests, nearest to the bot's own land first and then by value, and sends the nearest Mechanized Infantry that can get
there by combat move (never the last defender of a territory an enemy land unit could walk into). Where none can, it buys a
Mechanized Infantry at the purchase spot nearest the target (at most 3 a turn, targets within 2 hops of its land, so it can reach them the turn after), which
goes the turn after. There is no battle, so there is no risk to weigh.

Exceptions to "nothing is committed below the min", chosen so money is not left idle:

* **hold frontier** keeps the units and purchases it committed even when it cannot reach the min;
* **expand territory** and **control oceans** buy units toward a target they could not yet beat (at the purchase
  spots nearest it) and keep them;
* **pursue SC** keeps what it bought toward a target even when the force is not yet strong enough to advance.

## Land units and the water

Only Mechanized Infantry can enter the water (it becomes a Transport there); Infantry and Armor cannot, so the planner
never routes them through a sea zone (the engine's legal paths already refuse it) and never buys land units into a sea
space -- an amphibious plan is a Mechanized Infantry plan.

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
