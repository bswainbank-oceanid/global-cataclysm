"""
The strategy bots' turn planner: decides a whole turn -- purchases, combat moves, non-combat moves --
by working through objectives in priority order and committing the faction's resources to each.

Primary objectives, in this order, every turn:
    hold SCs -> capture SCs -> reinforce contested -> punish betrayers -> fill defensive gaps
Secondary objectives, in an order drawn by the style's weights each turn (a weight of 0 skips one):
    expand territory, hold frontier, control oceans, pursue the 1st / 2nd / 3rd SC target
A treacherous bot that has decided to withdraw first grabs allied territory (treasonous capture).

Every objective rates the chance of success with the fast battle simulator (bots/battle_sim.py: 200
samples, the battle run without a round limit) and applies its style's limits: with everything that could
be committed, if the chance is below the objective's MIN it is not pursued at all; otherwise resources
are added, one at a time, until the chance exceeds its MAX. Resources are units (each can be committed to
one objective), purchases (drawn by the faction's unit odds) and the treasury. What nobody claims stays put.

The planner never changes the game: it reads the real state and produces a Plan (purchase orders, combat
move orders, non-combat move orders). A 'full' plan is made at Purchase and is executed across the turn;
a 'noncombat' plan is made again after combat resolves, from the board as combat left it, and only moves.
"""
import heapq
import time

from ..engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from ..movement import (
    _is_ally_or_self, graph_distances, legal_air_move_destinations, legal_combat_move_paths,
    legal_noncombat_move_paths, trace_combat_move,
)
from ..state import FactionMode, UnitInstance, is_amphibious
from . import battle_sim
from .policy import excluded_naval_purchase_zones
from .strategy_settings import weighted_order

SC_BIAS = 8  # how much closer (in path-cost points) a Strategic Center purchase spot counts as, for where to buy

DEFAULT_BUDGET = 3500  # simulated battles per planning pass: about 2-3 seconds in a busy mid-game

LAND_POOL = ('Infantry', 'Mechanized Infantry', 'Armor')
AIR_POOL = ('Bomber', 'Fighter')
SEA_POOL = ('Aircraft Carrier', 'Cruiser', 'Submarine')
CATEGORY_POOL = {'Land': LAND_POOL, 'Air': AIR_POOL, 'Sea': SEA_POOL}


class Plan:
    """What a planning pass decided."""

    def __init__(self, planner):
        self.purchases = planner.purchase_orders()
        self.combat = planner.combat_orders()
        self.noncombat = planner.noncombat_orders()
        self.notes = list(planner.notes)
        self.elapsed = time.time() - planner.started
        self.evaluations = planner.evaluations


class Planner:
    def __init__(self, engine, faction, settings, style, rng, mode='full', budget=DEFAULT_BUDGET, samples=200, fast_samples=80,
                 hard_seconds=20.0):
        self.engine = engine
        self.gs = engine.game_state
        self.data = engine.data
        self.me = faction
        self.settings = settings
        self.style = style
        self.rng = rng
        self.mode = mode
        self.allow_combat = mode == 'full'
        self.allow_purchase = mode == 'full'
        self.samples = samples
        self.fast_samples = fast_samples
        self.started = time.time()
        self.evaluations = 0
        # Planning effort is budgeted in simulated battles, not seconds, so a seeded game replays exactly
        # (wall-clock cutoffs would not); 'hard_seconds' is only a safety net for something pathological.
        self.budget = budget
        self.sims_used = 0
        self._slice_limit = budget
        self.hard_deadline = self.started + hard_seconds
        self._cut = None  # the (min, max) an objective is comparing chances against, so the simulator can stop early

        self.unit_defs = self.data.units()
        self.terrs = self.data.territories()
        self.adjacency = self.data.adjacency()
        self.rules = self.data.rules()

        # the ledger
        self.claimed = {}       # unit_id -> objective that committed it
        self.stay = set()       # unit ids committed to staying where they are
        self.moves_combat = {}  # unit_id -> path
        self.moves_nc = {}      # unit_id -> destination
        self.purchases = {}     # (unit_type, territory) -> qty
        self.stubs = {}         # territory -> planned arrivals and purchases, as stand-in units
        self.treasury = self.gs.factions[faction].treasury_mpc if self.allow_purchase else 0
        self.notes = []

        self._stub_id = -1
        self._odds_cache = {}
        self._combat_paths = {}
        self._nc_dests = {}
        self._threat = None
        self._dest_cache = {}
        self._costs = {}
        self._excluded_zones = excluded_naval_purchase_zones(self.data)
        self._purchase_spots = None
        self._sc_spots = set()

        self.my_units = {}  # unit_id -> (unit, territory)
        for tid, t in self.gs.territories.items():
            for u in t.units:
                if u.owner == faction:
                    self.my_units[u.unit_id] = (u, tid)

    # ==== small helpers ======================================================================

    def out_of_time(self):
        return self.sims_used >= min(self._slice_limit, self.budget) or time.time() > self.hard_deadline

    def give(self, fraction):
        """The next objective may spend at most this fraction of the planning budget (unused budget carries on)."""
        self._slice_limit = self.sims_used + fraction * self.budget

    def category(self, u):
        return self.unit_defs[u.unit_type]['category']

    def cost(self, u):
        return self.unit_defs[u.unit_type].get('cost') or 0

    def allied(self, other):
        return other is not None and _is_ally_or_self(self.gs, self.me, other)

    def hostile(self, other):
        if other is None or self.allied(other):
            return False
        f = self.gs.factions[other]
        return f.mode in (FactionMode.HUMAN, FactionMode.BOT, FactionMode.DEFENSIVE) and not f.eliminated

    def capturable(self, other):
        """Whose land an attack can take: an enemy's, or that of a faction that has been eliminated -- its
        Strategic Centers and the rest are still there for the taking, and now likely undefended. (An ally's, a
        neutral faction's and one's own are not.)"""
        if other is None or self.allied(other):
            return False
        f = self.gs.factions[other]
        if f.mode == FactionMode.NEUTRAL:
            return False
        return f.eliminated or f.mode in (FactionMode.HUMAN, FactionMode.BOT, FactionMode.DEFENSIVE)

    def mobile_hostile(self, other):
        return self.hostile(other) and self.gs.factions[other].mode in (FactionMode.HUMAN, FactionMode.BOT)

    def is_land(self, tid):
        return self.terrs[tid]['type'] == 'land'

    def kind(self, tid):
        return 'land' if self.is_land(tid) else 'sea'

    def is_sc(self, tid):
        return self.is_land(tid) and self.gs.is_strategic_center(tid, self.terrs[tid])

    def value(self, tid):
        t = self.terrs[tid]
        return (t.get('value') or 0) + (2 if self.is_sc(tid) else 0)

    def owner(self, tid):
        return self.gs.territories[tid].owner

    def mine_at(self, tid):
        return [u for u in self.gs.territories[tid].units if u.owner == self.me]

    def enemies_at(self, tid):
        return [u for u in self.gs.territories[tid].units if self.hostile(u.owner)]

    def sea_zone_safe(self, tid):
        """True if a sea zone has nothing hostile in it and no contest under way -- the test for whether
        Mechanized Infantry may be purchased straight into it (rather than onto the land that funds it):
        it would land as an undefended Transport (defense 6, 1 HP), so this only happens somewhere quiet."""
        t = self.gs.territories[tid]
        if t.contested_by:
            return False
        return not any(not self.allied(u.owner) for u in t.units)

    def snapshot(self):
        return (dict(self.claimed), dict(self.moves_combat), dict(self.moves_nc), set(self.stay),
                dict(self.purchases), {t: list(v) for t, v in self.stubs.items()}, self.treasury)

    def restore(self, snap):
        self.claimed, self.moves_combat, self.moves_nc, self.stay, self.purchases, self.stubs, self.treasury = (
            dict(snap[0]), dict(snap[1]), dict(snap[2]), set(snap[3]), dict(snap[4]), {t: list(v) for t, v in snap[5].items()}, snap[6])

    def note(self, text):
        self.notes.append(text)

    # ==== movement reach =======================================================================

    def combat_paths(self, u, tid):
        got = self._combat_paths.get(u.unit_id)
        if got is None:
            if self.category(u) == 'Air':
                got = {d: [tid, d] for d in legal_air_move_destinations(u.unit_type, self.me, tid, 'combat', self.gs, self.data)}
            else:
                got = legal_combat_move_paths(u.unit_type, self.me, tid, self.gs, self.data)
            self._combat_paths[u.unit_id] = got
        return got

    def nc_dests(self, u, tid):
        got = self._nc_dests.get(u.unit_id)
        if got is None:
            if self.category(u) == 'Air':
                got = set(legal_air_move_destinations(u.unit_type, self.me, tid, 'noncombat', self.gs, self.data))
            else:
                got = set(legal_noncombat_move_paths(u.unit_type, self.me, tid, self.gs, self.data))
            self._nc_dests[u.unit_id] = got
        return got

    def free_for(self, u, how):
        """Can this unit of mine still be committed to a `how` ('combat' | 'nc') move?"""
        if u.unit_id in self.claimed:
            return False
        if how == 'combat':
            return self.allow_combat and not u.has_moved_combat
        if u.has_moved_noncombat:
            return False
        return self.category(u) == 'Air' or not u.has_moved_combat

    def threats(self, tid):
        """{hostile faction: its units that could combat-move onto `tid`} -- from where they stand now."""
        if self._threat is None:
            self._threat = {}
            for origin, t in self.gs.territories.items():
                for u in t.units:
                    if not self.mobile_hostile(u.owner):
                        continue
                    key = (u.owner, u.unit_type, origin)
                    dests = self._dest_cache.get(key)
                    if dests is None:
                        if self.unit_defs[u.unit_type]['category'] == 'Air':
                            dests = set(legal_air_move_destinations(u.unit_type, u.owner, origin, 'combat', self.gs, self.data))
                        else:
                            dests = set(legal_combat_move_paths(u.unit_type, u.owner, origin, self.gs, self.data))
                        self._dest_cache[key] = dests
                    for d in dests:
                        self._threat.setdefault(d, {}).setdefault(u.owner, []).append(u)
        return self._threat.get(tid, {})

    # ==== the odds ==============================================================================

    @staticmethod
    def _sig(units):
        return tuple(sorted((u.unit_type, u.current_hp, u.promotions, u.xp) for u in units))

    def attacker_odds(self, attackers, defenders, kind, bonus=None, fast=True, cut=None, samples=None):
        """The chance the attackers eliminate every defender and survive, run without a round limit."""
        if not attackers:
            return 0.0 if defenders else 1.0
        if not defenders:
            return 1.0
        samples = samples or (self.fast_samples if fast else self.samples)
        cut = cut if cut is not None else self._cut
        key = (kind, bonus, samples, self._sig(attackers), self._sig(defenders))
        got = self._odds_cache.get(key)
        if got is None:
            self.evaluations += 1
            odds = battle_sim.estimate(attackers, defenders, kind, self.unit_defs, self.rules, self.rng, samples, bonus, cut=cut)
            self.sims_used += odds.samples
            got = odds.attacker_wins
            self._odds_cache[key] = got
        return got

    def contest_odds(self, attackers, defenders, kind, bonus=None, fast=True, samples=None):
        """The chance an attack at least forces a contest: the attackers hang on through the 3 rounds of a battle
        (or win it outright) instead of being wiped out. This is what a Strategic Center attack's MIN risk is
        measured against; the max risk is still the chance of total victory (attacker_odds)."""
        if not attackers:
            return 0.0 if defenders else 1.0
        if not defenders:
            return 1.0
        samples = samples or (self.fast_samples if fast else self.samples)
        key = ('contest', kind, bonus, samples, self._sig(attackers), self._sig(defenders))
        got = self._odds_cache.get(key)
        if got is None:
            self.evaluations += 1
            odds = battle_sim.estimate(attackers, defenders, kind, self.unit_defs, self.rules, self.rng, samples, bonus, max_rounds=3)
            self.sims_used += odds.samples
            got = odds.attacker_wins + odds.contested
            self._odds_cache[key] = got
        return got

    def _has_land(self, units):
        return any(self.category(u) == 'Land' for u in units)

    def defenders_at(self, tid, claimed_only=True):
        """Who would be defending `tid`: allies there, my units committed to it (or all my units there
        that are not committed elsewhere, with claimed_only=False), the units already bought for it, and
        the ones the plan sends or buys."""
        out = []
        for u in self.gs.territories[tid].units:
            if u.owner == self.me:
                if u.unit_id in self.moves_combat or u.unit_id in self.moves_nc:
                    continue
                if claimed_only and u.unit_id not in self.stay:
                    continue
                out.append(u)
            elif self.allied(u.owner):
                out.append(u)
        out.extend(u for u in self.gs.territories[tid].pending_deployment if u.owner == self.me)
        out.extend(self.stubs.get(tid, []))
        out.extend(self.my_units[uid][0] for uid, dest in self.moves_nc.items() if dest == tid and self.my_units[uid][1] != tid)
        return out

    def hold_chance(self, tid, defenders, fast=True, samples=None):
        """1 - the chance the worst single threat takes `tid` (destroys the defenders, and for land brings a
        land unit to capture it) -- 1.0 when nothing can reach it."""
        threats = self.threats(tid)
        if not threats:
            return 1.0
        kind = self.kind(tid)
        worst = 0.0
        biggest = sorted(threats, key=lambda f: (-sum(self.cost(u) for u in threats[f]), f))[:2]  # the worst is among the strongest
        for faction in biggest:
            attackers = threats[faction]
            if kind == 'land' and not self._has_land(attackers):
                continue  # aircraft alone cannot take land
            cut = [1.0 - c for c in self._cut] if self._cut else None
            worst = max(worst, self.attacker_odds(attackers, defenders, kind, fast=fast, cut=cut, samples=samples))
        return 1.0 - worst

    # ==== resources ============================================================================

    def _stub(self, unit_type):
        self._stub_id -= 1
        return UnitInstance(unit_id=self._stub_id, unit_type=unit_type, owner=self.me,
                            current_hp=self.unit_defs[unit_type]['hp'])

    def purchase_orders(self):
        return [PurchaseOrder(t, q, tid) for (t, tid), q in sorted(self.purchases.items())]

    def combat_orders(self):
        return [CombatMoveOrder(uid, list(path)) for uid, path in sorted(self.moves_combat.items())]

    def noncombat_orders(self):
        return [NonCombatMoveOrder(uid, dest) for uid, dest in sorted(self.moves_nc.items())]

    def buy(self, tid, categories, name, prefer=None, only=None):
        """Buys one unit for `tid` by the faction's unit odds among the categories allowed (or, with `prefer`,
        that type first if it can be bought; with `only`, none but those types), if the treasury and the deploy
        capacity allow it. Returns the stand-in unit, or None.

        A Land-category unit can be bought at a sea target too, but only if it's amphibious (Mechanized
        Infantry -- it deploys straight there as a Transport, exactly as if it had walked into the water) and
        the zone is currently safe (sea_zone_safe) -- landing undefended among enemies would just lose it."""
        if not self.allow_purchase or tid in self._excluded_zones:
            return None
        contested = bool(self.gs.territories[tid].contested_by) and self.is_land(tid)
        sea_target = not self.is_land(tid)
        sea_ok = sea_target and self.sea_zone_safe(tid)
        candidates = []
        for cat in categories:
            for t in CATEGORY_POOL[cat]:
                d = self.unit_defs[t]
                if not d.get('purchasable'):
                    continue
                if contested and t != 'Infantry':
                    continue
                if sea_target and d['category'] == 'Land' and not (sea_ok and is_amphibious(d)):
                    continue
                if only is not None and t not in only:
                    continue
                candidates.append(t)
        if not candidates:
            return None
        weights = self.settings.unit_odds(self.me, candidates)
        if not any(w > 0 for w in weights):
            weights = [1] * len(candidates)
        order = weighted_order(self.rng, candidates, weights)
        if prefer in candidates:
            order = [prefer] + [t for t in order if t != prefer]
        for unit_type in order:
            key = (unit_type, tid)
            trial = dict(self.purchases)
            trial[key] = trial.get(key, 0) + 1
            orders = [PurchaseOrder(t, q, where) for (t, where), q in sorted(trial.items())]
            try:
                total, _ = self.engine._resolve_and_cost(orders, self.me)
            except ValueError:
                continue
            if total > self.gs.factions[self.me].treasury_mpc:
                continue
            self.purchases = trial
            self.treasury = self.gs.factions[self.me].treasury_mpc - total
            stub = self._stub(unit_type)
            self.stubs.setdefault(tid, []).append(stub)
            return stub
        return None

    def buy_toward(self, dist, categories, name, only=None):
        """Buys one unit at a purchase spot near a target (`dist`: cost of the cheapest path from each square to
        the target), by the faction's unit odds among the categories. Strategic Centers are favoured spots: a unit
        bought there costs less (the SC price) and there is room for two more, and it is a garrison where it matters --
        so a spot that is (or is paid for by) a Strategic Center counts SC_BIAS squares-worth closer than it is."""
        if not self.allow_purchase:
            return None
        if self._purchase_spots is None:
            sc_spots, other_spots = self.engine.legal_purchase_targets(self.me)
            self._sc_spots = set(sc_spots)
            self._purchase_spots = sorted(set(sc_spots) | set(other_spots))
        spots = sorted(self._purchase_spots,
                       key=lambda t: (dist.get(t, 1 << 30) - (SC_BIAS if t in self._sc_spots else 0), t))
        for tid in spots[:10]:
            stub = self.buy(tid, categories, name, only=only)
            if stub is not None:
                return stub
        return None

    def claim(self, u, objective):
        self.claimed[u.unit_id] = objective

    # ==== the builders ===========================================================================

    def secure(self, tid, name, limits, resources=('stay', 'purchase', 'nc'), categories=('Land', 'Air'), keep_on_fail=False):
        """Make `tid` safe from the worst threat: commit the units in it, then purchases, then non-combat
        moves, until the hold chance passes the objective's max; if it can't reach the min, commit nothing."""
        lo, hi = limits
        if not self.threats(tid) or self.out_of_time():
            return True
        self._cut = (lo, hi)
        snap = self.snapshot()
        defenders = self.defenders_at(tid)
        p = self.hold_chance(tid, defenders)
        if p >= hi:
            return True
        if 'stay' in resources:
            here = sorted((u for u in self.mine_at(tid) if u.unit_id not in self.claimed and u.unit_id not in self.stay
                           and self.category(u) in categories), key=lambda u: (self.cost(u), u.unit_id))
            for k, u in enumerate(here):
                if p >= hi or self.out_of_time():
                    break
                self.claim(u, name)
                self.stay.add(u.unit_id)
                defenders.append(u)
                if self._recheck(k, p, hi):
                    p = self.hold_chance(tid, defenders)
        if p < hi and 'purchase' in resources and self.is_land(tid):
            k = 0
            while p < hi and not self.out_of_time():
                stub = self.buy(tid, categories, name)
                if stub is None:
                    break
                defenders.append(stub)
                if self._recheck(k, p, hi):
                    p = self.hold_chance(tid, defenders)
                k += 1
        if p < hi and 'nc' in resources:
            movers = []
            for uid, (u, origin) in self.my_units.items():
                if origin == tid or self.category(u) not in categories or not self.free_for(u, 'nc'):
                    continue
                if tid in self.nc_dests(u, origin):
                    movers.append((u, origin))
            movers.sort(key=lambda m: (self.cost(m[0]), m[0].unit_id))
            for k, (u, origin) in enumerate(movers):
                if p >= hi or self.out_of_time():
                    break
                self.claim(u, name)
                self.moves_nc[u.unit_id] = tid
                defenders.append(u)
                if self._recheck(k, p, hi):
                    p = self.hold_chance(tid, defenders)
        # (p may be stale after the last unmeasured additions; the final check below is a fresh one)
        p = self.hold_chance(tid, defenders, fast=False)
        if p < lo:
            if keep_on_fail:  # defending as well as it can beats leaving the money unspent
                self.note(f'{name}: {self.terrs[tid]["name"]} defended as far as possible ({p:.0%})')
                return False
            self.restore(snap)
            self.note(f'{name}: {self.terrs[tid]["name"]} not held (only {p:.0%})')
            return False
        self.note(f'{name}: {self.terrs[tid]["name"]} held at {p:.0%}')
        return True

    @staticmethod
    def _recheck(k, p, hi):
        """Measure the chance again after adding a unit? Far from the goal, add several between measurements."""
        gap = hi - p
        step = 1 if gap < 0.2 else 2 if gap < 0.5 else 3
        return (k + 1) % step == 0

    def _bonus_side(self, tid, land_attackers, crossed):
        t = self.gs.territories[tid]
        if t.reclaim_bonus_for == self.me:
            return 'attacker'
        if self.me in t.ambush_bonus_for:
            return 'attacker'
        if land_attackers and all(crossed.get(u.unit_id) for u in land_attackers):
            return 'defender'
        return None

    def assess_assault(self, tid, categories, contest=False):
        """Chance of taking `tid` with every unit that could reach it (for ordering targets); with `contest`, the
        chance of at least forcing a contest (hanging on through 3 rounds)."""
        defenders = self.enemies_at(tid)
        attackers, crossed = self._attack_candidates(tid, categories)
        attackers = self.mine_at(tid) + [u for u, _ in attackers]
        if self.is_land(tid) and not self._has_land(attackers):
            return 0.0
        bonus = self._bonus_side(tid, [u for u in attackers if self.category(u) == 'Land'], crossed)
        if contest:
            return self.contest_odds(attackers, defenders, self.kind(tid), bonus)
        return self.attacker_odds(attackers, defenders, self.kind(tid), bonus)

    def _attack_candidates(self, tid, categories):
        out, crossed = [], {}
        for uid, (u, origin) in self.my_units.items():
            if origin == tid or self.category(u) not in categories or not self.free_for(u, 'combat'):
                continue
            path = self.combat_paths(u, origin).get(tid)
            if path is None:
                continue
            out.append((u, path))
            if self.category(u) == 'Land' and len(path) > 1:
                try:
                    crossed[u.unit_id] = trace_combat_move(u.unit_type, self.me, path, self.gs, self.data).crossed_water
                except ValueError:
                    crossed[u.unit_id] = False
        out.sort(key=lambda c: (self.cost(c[0]), c[0].unit_id))
        return out, crossed

    def assault(self, tid, name, limits, categories=('Land', 'Air'), purchase_infantry=False, min_is_contest=False):
        """Take (or win the fight at) `tid` by combat movement: with every unit that could get there, if the
        chance is under the min give up; else add units cheapest-first until it passes the max. With
        `min_is_contest` (attacking a Strategic Center) the min risk is the chance the attack at least forces a
        contest -- hangs on through 3 rounds -- while the max is still the chance of total victory."""
        lo, hi = limits
        if not self.allow_combat or self.out_of_time():
            return False
        self._cut = (lo, hi)
        snap = self.snapshot()
        kind = self.kind(tid)
        defenders = self.enemies_at(tid)
        present = [u for u in self.mine_at(tid) if u.unit_id not in self.moves_combat and u.unit_id not in self.moves_nc]
        candidates, crossed = self._attack_candidates(tid, categories)
        if not candidates and not present:
            return False

        def chance(units, fast=True):
            land = [u for u in units if self.category(u) == 'Land']
            if kind == 'land' and not land and self.capturable(self.owner(tid)):
                return 0.0  # aircraft alone cannot take land
            return self.attacker_odds(units, defenders, kind, self._bonus_side(tid, land, crossed), fast=fast)

        def floor(units, fast=True):
            """What the min risk is measured against."""
            if not min_is_contest:
                return chance(units, fast)
            land = [u for u in units if self.category(u) == 'Land']
            if kind == 'land' and not land and self.capturable(self.owner(tid)):
                return 0.0
            return self.contest_odds(units, defenders, kind, self._bonus_side(tid, land, crossed), fast=fast)

        if candidates and floor(present + [u for u, _ in candidates]) < lo:
            return False
        attackers = list(present)
        chosen = []
        p = chance(attackers) if attackers else 0.0
        for u, path in candidates:
            if p >= hi or self.out_of_time():
                break
            attackers.append(u)
            chosen.append((u, path))
            p = chance(attackers)
        # capturing land needs a land unit in the attack; aircraft alone can only fight
        if kind == 'land' and self.capturable(self.owner(tid)) and not self._has_land(attackers):
            spare = [(u, path) for u, path in candidates if (u, path) not in chosen and self.category(u) == 'Land']
            if not spare:
                self.restore(snap)
                return False
            chosen.append(spare[0])
            attackers.append(spare[0][0])
        if p < hi and purchase_infantry and self.allow_purchase and self.is_land(tid) and self.owner(tid) == self.me:
            while p < hi and not self.out_of_time():
                stub = self.buy(tid, ('Land',), name)
                if stub is None:
                    break
                attackers.append(stub)
                p = chance(attackers)
        p = chance(attackers, fast=False)
        p_floor = floor(attackers, fast=False) if min_is_contest else p
        if p_floor < lo:
            self.restore(snap)
            self.note(f'{name}: {self.terrs[tid]["name"]} not pursued ({p_floor:.0%}{" to force a contest" if min_is_contest else ""})')
            return False
        for u in present:
            self.claim(u, name)
            self.stay.add(u.unit_id)
        for u, path in chosen:
            self.claim(u, name)
            self.moves_combat[u.unit_id] = path
        self.note(f'{name}: attacking {self.terrs[tid]["name"]} with {len(attackers)} units at {p:.0%}')
        return True

    # ==== the primary objectives =================================================================

    def objective_hold_scs(self):
        limits = self.settings.limits(self.style, 'hold_sc')
        mine = [tid for tid in self.terrs if self.is_sc(tid) and self.owner(tid) == self.me]
        ranked = []
        for tid in mine:
            if not self.threats(tid):
                continue
            ranked.append((self.hold_chance(tid, self.defenders_at(tid, claimed_only=False), samples=30), tid))
        for _, tid in sorted(ranked):  # the most threatened first
            self.secure(tid, 'hold_sc', limits)

    def objective_capture_scs(self):
        if not self.allow_combat:
            return
        limits = self.settings.limits(self.style, 'capture_sc')
        # (an eliminated faction's old Strategic Centers stay targets: they may well be empty)
        targets = [tid for tid in self.terrs if self.is_sc(tid) and self.capturable(self.owner(tid))]
        ranked = []
        for tid in targets:
            if self.out_of_time():
                break
            ranked.append((-self.assess_assault(tid, ('Land', 'Air', 'Sea')), tid, self.assess_assault(tid, ('Land', 'Air', 'Sea'), contest=True)))
        for neg, tid, contest in sorted(ranked):
            if contest < limits[0]:
                continue  # the min risk: the chance the attack at least forces a contest
            self.assault(tid, 'capture_sc', limits, ('Land', 'Air', 'Sea'), min_is_contest=True)

    def objective_reinforce_contested(self):
        if not self.allow_combat:
            return
        limits = self.settings.limits(self.style, 'reinforce_contested')
        targets = [tid for tid, t in self.gs.territories.items() if t.contested_by and self.me in t.contested_by]
        ranked = sorted((-self.assess_assault(tid, ('Land', 'Air', 'Sea')), tid) for tid in targets if not self.out_of_time())
        for _, tid in ranked:
            self.assault(tid, 'reinforce_contested', limits, ('Land', 'Air', 'Sea'), purchase_infantry=True)

    def objective_punish_betrayers(self):
        if not self.allow_combat:
            return
        limits = self.settings.limits(self.style, 'punish_betrayers')
        targets = [tid for tid, t in self.gs.territories.items()
                   if t.reclaim_bonus_for == self.me and self.enemies_at(tid)]
        ranked = sorted((-self.assess_assault(tid, ('Land', 'Air', 'Sea')), tid) for tid in targets if not self.out_of_time())
        for _, tid in ranked:
            self.assault(tid, 'punish_betrayers', limits, ('Land', 'Air', 'Sea'), purchase_infantry=True)

    def objective_fill_gaps(self):
        """Every land territory of mine that nothing defends but an enemy land unit could walk into gets a unit:
        a move-in if one is free, else a purchase (Infantry preferred)."""
        for tid in sorted(self.terrs):
            if self.out_of_time():
                return
            if not self.is_land(tid) or self.owner(tid) != self.me:
                continue
            if self.defenders_at(tid, claimed_only=False):
                continue
            if self.enemies_at(tid):  # enemy units here: this is a contest, not a gap
                continue
            reachable = any(self.category(u) == 'Land' for units in self.threats(tid).values() for u in units)
            if not reachable:
                continue
            movers = []
            for uid, (u, origin) in self.my_units.items():
                if self.category(u) != 'Land' or not self.free_for(u, 'nc') or origin == tid:
                    continue
                if tid in self.nc_dests(u, origin):
                    remaining_at_origin = [v for v in self.defenders_at(origin, claimed_only=False) if v.unit_id != u.unit_id]
                    movers.append((0 if u.unit_type == 'Infantry' else 1, 0 if remaining_at_origin else 1, self.cost(u), u.unit_id, u, origin))
            movers.sort(key=lambda m: m[:4])
            filled = False
            # (units are moved only if that leaves nobody's territory empty behind them; else prefer buying)
            for pref, leaves_empty, _, _, u, origin in movers:
                if leaves_empty:
                    continue
                self.claim(u, 'fill_gap')
                self.moves_nc[u.unit_id] = tid
                filled = True
                self.note(f'fill_gap: {self.terrs[tid]["name"]} garrisoned by a {u.unit_type}')
                break
            if not filled and self.allow_purchase:
                stub = self.buy(tid, ('Land',), 'fill_gap', prefer='Infantry')
                if stub is not None:
                    self.note(f'fill_gap: {self.terrs[tid]["name"]} garrisoned by a purchased {stub.unit_type}')

    def objective_treasonous_capture(self):
        """A treacherous bot that has decided to withdraw takes allied land it can reach by non-combat move."""
        limits = self.settings.limits(self.style, 'expand_territory')
        targets = []
        for tid, t in self.gs.territories.items():
            if not self.is_land(tid) or t.owner is None or t.owner == self.me or not self.allied(t.owner):
                continue
            if self.is_sc(tid):
                continue  # units on an allied Strategic Center would lock the withdrawal
            targets.append((-self.value(tid), tid))
        for _, tid in sorted(targets):
            if self.out_of_time():
                return
            defenders = [u for u in self.gs.territories[tid].units if u.owner != self.me]
            movers = []
            for uid, (u, origin) in self.my_units.items():
                if self.category(u) not in ('Land', 'Air') or not self.free_for(u, 'nc') or origin == tid:
                    continue
                if tid in self.nc_dests(u, origin):
                    movers.append((self.cost(u), u.unit_id, u))
            movers.sort(key=lambda m: m[:2])
            if not movers:
                continue
            everything = [u for _, _, u in movers]
            if self.attacker_odds(everything, defenders, 'land') < limits[0]:
                continue
            force = []
            p = 0.0
            for _, _, u in movers:
                force.append(u)
                p = self.attacker_odds(force, defenders, 'land')
                if p >= limits[1]:
                    break
            if not self._has_land(force):
                continue
            for u in force:
                self.claim(u, 'treasonous_capture')
                self.moves_nc[u.unit_id] = tid
            self.note(f'treasonous_capture: moving {len(force)} units into {self.terrs[tid]["name"]} ({p:.0%})')

    # ==== the secondary objectives ================================================================

    def objective_expand_territory(self):
        if not self.allow_combat:
            return
        limits = self.settings.limits(self.style, 'expand_territory')
        targets = []
        for tid, t in self.gs.territories.items():
            if self.is_land(tid) and self.capturable(t.owner) and not self.is_sc(tid):
                targets.append((-self.value(tid), tid))
        failed = []
        for _, tid in sorted(targets):
            if self.out_of_time():
                break
            if not self.assault(tid, 'expand_territory', limits, ('Land', 'Air')):
                failed.append(tid)
        # what could not be taken yet is what to build toward: buy units for it, at Strategic Centers by preference
        for tid in [t for t in failed if self._near_my_land(t)][:2]:
            if self.out_of_time():
                return
            self._build_up(tid, ('Land', 'Air'), 'expand_territory', limits)

    def _near_my_land(self, tid, hops=3):
        return self._my_land_within(tid, hops)

    def _build_up(self, target, categories, name, limits):
        """Buy units toward a target that is still too strong for the force that could reach it: keep buying (at the
        purchase spots nearest it, Strategic Centers favoured) until the force, counting what was bought, would
        pass the objective's max. The purchases stay whether or not it gets there."""
        if not self.allow_purchase or self.out_of_time():
            return 0
        self._cut = limits
        kind = self.kind(target)
        dist = self.costs_from(target)[0]
        defenders = self.enemies_at(target)
        cands, crossed = self._attack_candidates(target, categories)
        force = self.mine_at(target) + [u for u, _ in cands]
        p = self.attacker_odds(force, defenders, kind) if force else 0.0
        bought = 0
        while p < limits[1] and not self.out_of_time():
            stub = self.buy_toward(dist, categories, name)
            if stub is None:
                break
            force.append(stub)
            bought += 1
            if self._recheck(bought - 1, p, limits[1]):
                p = self.attacker_odds(force, defenders, kind)
        if bought:
            self.note(f'{name}: {bought} units bought toward {self.terrs[target]["name"]}')
        return bought

    def objective_hold_frontier(self):
        limits = self.settings.limits(self.style, 'hold_frontier')
        targets = []
        for tid in self.terrs:
            if self.is_land(tid) and self.owner(tid) == self.me and not self.is_sc(tid) and self.threats(tid):
                targets.append((-self.value(tid), tid))
        for _, tid in sorted(targets):
            if self.out_of_time():
                return
            self.secure(tid, 'hold_frontier', limits, keep_on_fail=True)

    def _my_land_within(self, tid, hops):
        dist = graph_distances(tid, self.data)
        return any(self.is_land(t) and self.owner(t) == self.me for t, d in dist.items() if d <= hops)

    def objective_control_oceans(self):
        limits = self.settings.limits(self.style, 'control_oceans')
        stacks = []
        for tid in self.terrs:
            if self.is_land(tid):
                continue
            ships = [u for u in self.enemies_at(tid) if self.category(u) == 'Sea']
            if ships and self._my_land_within(tid, 3):
                stacks.append((-sum(self.cost(u) for u in ships), tid))
        below = []
        for _, tid in sorted(stacks):
            if self.out_of_time():
                break
            if not self.assault(tid, 'control_oceans', limits, ('Sea', 'Air')):
                below.append(tid)
        # the stacks it could not beat (the biggest first): buy ships and planes toward them
        for tid in below[:2]:
            if self.out_of_time():
                break
            self._build_up(tid, ('Sea', 'Air'), 'control_oceans', limits)
        self._bombard_idle_cruisers('control_oceans', limits)
        self._second_pass_fleets(limits)

    def _bombard_idle_cruisers(self, name, limits):
        """rules.json's combat.cruiser_bombardment: a Cruiser left with no worthwhile enemy fleet
        to attack above (no stack in reach, or one not worth the risk) seeks an occupied enemy
        land space to bombard instead -- a free attack roll at the very start of Combat
        Resolution, since it never actually enters the land and takes no counter-fire. Skipped
        for a fleet threatened badly enough that it would rather retreat to safety
        (_second_pass_fleets, right after this) -- bombarding is still a combat move, and claims
        the unit's one move for the whole turn same as any other. Any idle Submarine/Aircraft
        Carrier sharing that sea zone -- the rest of the stack -- rides along as an escort
        (GameEngine._execute_combat_moves' bombarded_this_batch mechanism): it sails to the
        Cruiser's own final position without attacking anything itself, rather than being left
        behind doing nothing."""
        if not self.allow_combat:
            return
        by_zone = {}
        for uid, (u, origin) in self.my_units.items():
            if self.category(u) == 'Sea' and not self.is_land(origin) and self.free_for(u, 'combat'):
                by_zone.setdefault(origin, []).append(u)
        for origin in sorted(by_zone):
            if self.out_of_time():
                return
            fleet = by_zone[origin]
            cruisers = [u for u in fleet if u.unit_type == 'Cruiser']
            if not cruisers:
                continue
            if self.threats(origin) and self.hold_chance(origin, self.defenders_at(origin, claimed_only=False)) < limits[0]:
                continue  # this fleet would rather try to retreat to safety than stick around to bombard
            for cruiser in cruisers:
                if self.out_of_time():
                    return
                paths = self.combat_paths(cruiser, origin)
                targets = sorted((d for d in paths if self.is_land(d)),
                                  key=lambda d: -sum(self.cost(e) for e in self.enemies_at(d)))
                if not targets:
                    continue
                target = targets[0]
                path = paths[target]
                self.claim(cruiser, name)
                self.moves_combat[cruiser.unit_id] = path
                self.note(f'{name}: the Cruiser at {self.terrs[origin]["name"]} bombards {self.terrs[target]["name"]}')
                for escort in fleet:
                    if escort.unit_type == 'Cruiser' or not self.free_for(escort, 'combat'):
                        continue
                    self.claim(escort, name)
                    self.moves_combat[escort.unit_id] = path

    def _second_pass_fleets(self, limits):
        """Fleets of mine that a stronger enemy could destroy move to safety: an adjacent-to-friendly-land sea
        zone, preferring one beside a friendly Strategic Center."""
        by_zone = {}
        for uid, (u, origin) in self.my_units.items():
            if self.category(u) == 'Sea' and not self.terrs[origin]['type'] == 'land' and self.free_for(u, 'nc'):
                by_zone.setdefault(origin, []).append(u)
        for zone in sorted(by_zone):
            if self.out_of_time():
                return
            fleet = by_zone[zone]
            if not self.threats(zone):
                continue
            here = self.defenders_at(zone, claimed_only=False)
            if self.hold_chance(zone, here) >= limits[0]:
                continue
            options = None
            for u in fleet:
                dests = self.nc_dests(u, zone)
                options = set(dests) if options is None else options & dests
            best = None
            for dest in sorted(options or ()):
                if self.is_land(dest):
                    continue
                touches = [n for n in self.adjacency.get(dest, []) if self.is_land(n) and self.owner(n) == self.me]
                if not touches:
                    continue
                safe = self.hold_chance(dest, fleet)
                score = (safe >= limits[0], any(self.is_sc(n) for n in touches), safe)
                if best is None or score > best[0]:
                    best = (score, dest)
            if best is not None:
                for u in fleet:
                    self.claim(u, 'control_oceans')
                    self.moves_nc[u.unit_id] = best[1]
                self.note(f'control_oceans: fleet at {self.terrs[zone]["name"]} withdraws to {self.terrs[best[1]]["name"]}')

    # -- pursuing Strategic Centers ---------------------------------------------------------------------

    def square_cost(self, tid):
        costs = self.settings.distance_costs
        if not self.is_land(tid):
            return costs['sea']
        owner = self.owner(tid)
        if owner == self.me:
            return costs['friendly']
        if owner is None:
            return costs['friendly']
        if self.gs.factions[owner].mode == FactionMode.NEUTRAL:
            return None
        if self.allied(owner):
            return costs['allied']
        return costs['enemy']

    def costs_from(self, src):
        """Cheapest path costs from `src` (the cost of a path is the sum of its squares' costs, the start
        excluded), and each square's predecessor."""
        got = self._costs.get(src)
        if got is not None:
            return got
        dist, prev = {src: 0}, {}
        heap = [(0, src)]
        while heap:
            d, node = heapq.heappop(heap)
            if d > dist.get(node, 1 << 30):
                continue
            for n in self.adjacency.get(node, []):
                c = self.square_cost(n)
                if c is None:
                    continue
                nd = d + c
                if nd < dist.get(n, 1 << 30):
                    dist[n] = nd
                    prev[n] = node
                    heapq.heappush(heap, (nd, n))
        self._costs[src] = (dist, prev)
        return self._costs[src]

    def sc_targets(self):
        """The enemy Strategic Centers, easiest first, as (challenge number, target, the path from my nearest SC)."""
        mine = [t for t in self.terrs if self.is_sc(t) and self.owner(t) == self.me]
        enemy = [t for t in self.terrs if self.is_sc(t) and self.capturable(self.owner(t))]
        best = {}
        for src in mine:
            dist, prev = self.costs_from(src)
            for dst in enemy:
                if dst not in dist:
                    continue
                path = [dst]
                while path[-1] != src:
                    path.append(prev[path[-1]])
                path.reverse()
                number = dist[dst] + self._path_enemy_value(dst, path)
                if dst not in best or number < best[dst][0]:
                    best[dst] = (number, dst, path)
        return sorted(best.values())

    def _path_enemy_value(self, target, path):
        """Total value of the enemy units within two spaces of the target and of every square on the path
        (sea units only where they are on the path itself)."""
        squares = set(path)
        seen = set()
        total = 0
        for centre in [target] + list(path):
            for tid, d in graph_distances(centre, self.data).items():
                if d > 2 or tid in seen:
                    continue
                for u in self.gs.territories[tid].units:
                    if not self.hostile(u.owner):
                        continue
                    if self.category(u) == 'Sea' and tid not in squares:
                        continue
                    seen.add((tid, u.unit_id))
                    total += self.cost(u)
                seen.add(tid)
        return total

    def objective_pursue_sc(self, rank, active=(0, 1, 2)):
        """Direct the units toward Strategic Center target `rank`: every free unit for which it is the nearest of
        this turn's targets marches on it (attacking enemies on the way when the odds are right), and units are
        bought at the spot nearest the target until the force could take it. If even that is short of the min,
        nobody advances yet -- but what was bought stays, building the force."""
        name = f'pursue_sc_{rank + 1}'
        limits = self.settings.limits(self.style, name)
        if self._sc_targets is None:
            self._sc_targets = self.sc_targets()
        ranks = [r for r in active if r < len(self._sc_targets)]
        if rank not in ranks or self.out_of_time():
            return
        number, target, path = self._sc_targets[rank]
        dist_maps = {r: self.costs_from(self._sc_targets[r][1])[0] for r in ranks}
        dist_to_target = dist_maps[rank]
        defenders = self._region_enemies(target)
        cand = []
        for uid, (u, origin) in self.my_units.items():
            if u.unit_id in self.claimed:
                continue
            nearest = min(((dist_maps[r].get(origin, 1 << 30), r) for r in ranks))
            if nearest[1] != rank or nearest[0] >= 1 << 30:
                continue
            cand.append((dist_to_target[origin], self.cost(u), u.unit_id, u, origin))
        cand.sort(key=lambda c: c[:3])
        self._cut = limits
        force = [c[3] for c in cand]
        strongest = sorted((u for u in force if self.category(u) != 'Sea'), key=lambda u: (-self.cost(u), u.unit_id))[:20]
        fighters = list(strongest)
        p = self.attacker_odds(fighters, defenders, 'land') if fighters else 0.0
        bought = []
        k = 0
        while p < limits[1] and not self.out_of_time():
            stub = self.buy_toward(dist_to_target, ('Land', 'Air', 'Sea'), name)
            if stub is None:
                break
            bought.append(stub)
            if self.category(stub) != 'Sea':
                fighters.append(stub)
            if self._recheck(k, p, limits[1]):
                p = self.attacker_odds(fighters, defenders, 'land')
            k += 1
        p = self.attacker_odds(fighters, defenders, 'land', fast=False) if fighters else 0.0
        if p < limits[0]:
            self.note(f'{name}: {self.terrs[target]["name"]} not yet ({p:.0%}); building up with {len(bought)} purchases')
            return
        moved = 0
        on_path = set(path)
        for u in force:
            if self.out_of_time():
                break
            origin = self.my_units[u.unit_id][1]
            if self._advance(u, origin, target, dist_to_target, on_path, limits):
                moved += 1
        if moved:
            self.note(f'{name}: {moved} units advance on {self.terrs[target]["name"]} (challenge {number})')

    def objective_pursue_leftovers(self):
        """Whatever nobody claimed goes after the Strategic Centers, every turn, overkill welcome: every free
        unit marches on the nearest of the (up to three) targets, and all the treasury still left is spent on units
        bought toward them (Strategic Center spots favoured), taking the targets in turn. Only a unit that is the
        last defender of a territory an enemy land unit could walk into stays behind."""
        if self._sc_targets is None:
            self._sc_targets = self.sc_targets()
        targets = self._sc_targets[:3]
        if not targets or self.out_of_time():
            return
        ranks = list(range(len(targets)))
        dist_maps = {r: self.costs_from(targets[r][1])[0] for r in ranks}
        paths = {r: set(targets[r][2]) for r in ranks}
        limits = self.settings.limits(self.style, 'pursue_sc_1')
        moved = 0
        for uid, (u, origin) in sorted(self.my_units.items()):
            if u.unit_id in self.claimed or self.out_of_time():
                continue
            nearest = min((dist_maps[r].get(origin, 1 << 30), r) for r in ranks)
            if nearest[0] >= 1 << 30:
                continue
            if self.is_land(origin) and self.owner(origin) == self.me and self.category(u) == 'Land':
                others = [v for v in self.defenders_at(origin, claimed_only=False) if v.unit_id != u.unit_id]
                if not others and any(self.category(v) == 'Land' for units in self.threats(origin).values() for v in units):
                    continue
            r = nearest[1]
            if self._advance(u, origin, targets[r][1], dist_maps[r], paths[r], limits):
                moved += 1
        bought, k = 0, 0
        while self.allow_purchase and not self.out_of_time():
            r = ranks[k % len(ranks)]
            stub = self.buy_toward(dist_maps[r], ('Land', 'Air', 'Sea'), 'pursue_leftovers')
            if stub is None:
                # nothing more can be bought toward this target (spots full or the money is gone): try the others once
                if all(self.buy_toward(dist_maps[q], ('Land', 'Air', 'Sea'), 'pursue_leftovers') is None for q in ranks):
                    break
                bought += 1
            else:
                bought += 1
            k += 1
        if moved or bought:
            self.note(f'pursue_leftovers: {moved} more units advance, {bought} bought toward the Strategic Centers')

    # -- Empty Land Grab ------------------------------------------------------------------------------

    GRAB_PURCHASES = 3  # at most this many Mechanized Infantry bought per turn for grabs
    GRAB_REACH = 6      # a target this many hops from my land or closer is worth buying a Mech Inf toward

    def _undefended_land(self):
        """Land of an enemy (or an eliminated faction) that nobody defends and nobody contests, nearest my
        own land first, then the most valuable: [(hops from my land, -value, territory id)]."""
        mine = [tid for tid in self.terrs if self.is_land(tid) and self.owner(tid) == self.me]
        if not mine:
            return []
        dist = {tid: 0 for tid in mine}
        frontier = list(mine)
        while frontier:
            nxt = []
            for cur in frontier:
                for n in self.adjacency.get(cur, []):
                    if n not in dist:
                        dist[n] = dist[cur] + 1
                        nxt.append(n)
            frontier = nxt
        already = {path[-1] for path in self.moves_combat.values()}
        out = []
        for tid, d in dist.items():
            if not self.is_land(tid) or tid in already:
                continue
            t = self.gs.territories[tid]
            if not self.capturable(t.owner) or t.contested_by:
                continue
            if any(not self.allied(u.owner) for u in t.units) or any(u.owner == self.me for u in t.units):
                continue  # somebody is there
            out.append((d, -self.value(tid), tid))
        return sorted(out)

    def objective_empty_land_grab(self):
        """Mechanized Infantry take undefended territory. Looks for enemy land nobody is defending, nearest to
        my own land first and then by value; sends the nearest Mech Inf that can get there (never the last
        defender of a territory an enemy could walk into), and where none can and the target is within
        GRAB_REACH (6) hops, buys one at the purchase spot nearest the target -- it goes the turn after.
        buy_toward already ranks a safe sea zone right next to the target above a land spot further off
        (square_cost's sea cost is close to a friendly hop's), so a distant or island target often gets its
        Mech Inf bought straight into the water -- one hop closer, one turn sooner -- rather than on land where
        it would still have to walk to the coast first (buy's own amphibious/sea_zone_safe check keeps this to
        genuinely quiet water). There is no battle here, so no risk to weigh."""
        if not self.allow_combat and not self.allow_purchase:
            return
        sent = bought = 0
        for hops, _, tid in self._undefended_land():
            if self.out_of_time():
                break
            best = None
            if self.allow_combat:
                for uid, (u, origin) in sorted(self.my_units.items()):
                    if u.unit_type != 'Mechanized Infantry' or not self.free_for(u, 'combat') or origin == tid:
                        continue
                    path = self.combat_paths(u, origin).get(tid)
                    if path is None:
                        continue
                    if self.is_land(origin) and self.owner(origin) == self.me:
                        others = [v for v in self.defenders_at(origin, claimed_only=False) if v.unit_id != u.unit_id]
                        if not others and any(self.category(v) == 'Land' for units in self.threats(origin).values() for v in units):
                            continue
                    if best is None or (len(path), uid) < (len(best[1]), best[0].unit_id):
                        best = (u, path)
            if best is not None:
                u, path = best
                self.claim(u, 'empty_land_grab')
                self.moves_combat[u.unit_id] = path
                sent += 1
                self.note(f'empty_land_grab: a Mech Inf takes {self.terrs[tid]["name"]}')
            elif self.allow_purchase and bought < self.GRAB_PURCHASES and hops <= self.GRAB_REACH:
                stub = self.buy_toward(self.costs_from(tid)[0], ('Land',), 'empty_land_grab', only=('Mechanized Infantry',))
                if stub is not None:
                    bought += 1
                    self.note(f'empty_land_grab: a Mech Inf bought toward {self.terrs[tid]["name"]}')

    def _region_enemies(self, target):
        out = []
        for tid, d in graph_distances(target, self.data).items():
            if d <= 2:
                out.extend(u for u in self.gs.territories[tid].units if self.hostile(u.owner) and self.category(u) != 'Sea')
        return out

    def _advance(self, u, origin, target, dist_to_target, on_path, limits):
        """One unit's move toward the target: a combat move onto a path square that holds enemies if that
        looks good, else the non-combat step that gets closest."""
        here = dist_to_target.get(origin, 1 << 30)
        if self.allow_combat and self.free_for(u, 'combat'):
            for dest, path in sorted(self.combat_paths(u, origin).items()):
                if dest in on_path and dest != origin and self.enemies_at(dest) and dist_to_target.get(dest, 1 << 30) < here:
                    if self.assault(dest, 'pursue_sc', limits, ('Land', 'Air')) and u.unit_id in self.claimed:
                        return True
                    break
        if not self.free_for(u, 'nc'):
            return False
        best = None
        for dest in sorted(self.nc_dests(u, origin)):
            d = dist_to_target.get(dest)
            if d is None or d >= here:
                continue
            if best is None or d < best[0]:
                best = (d, dest)
        if best is None:
            return False
        self.claim(u, 'pursue_sc')
        self.moves_nc[u.unit_id] = best[1]
        return True

    _sc_targets = None

    # ==== the whole plan =========================================================================

    def run(self, treasonous=False):
        self.give(0.35)
        self.objective_hold_scs()
        self.give(0.15)
        self.objective_capture_scs()
        self.give(0.10)
        self.objective_reinforce_contested()
        self.give(0.05)
        self.objective_punish_betrayers()
        self.give(0.05)
        self.objective_fill_gaps()
        if treasonous:
            self.give(0.10)
            self.objective_treasonous_capture()
        order = self.settings.secondary_order(self.style, self.rng)
        active = [int(n[-1]) - 1 for n in order if n.startswith('pursue_sc_')]
        for name in order:
            self.give(0.12)
            if self.out_of_time():
                break
            if name == 'expand_territory':
                self.objective_expand_territory()
            elif name == 'hold_frontier':
                self.objective_hold_frontier()
            elif name == 'control_oceans':
                self.objective_control_oceans()
            elif name.startswith('pursue_sc_'):
                self.objective_pursue_sc(int(name[-1]) - 1, active)
            elif name == 'empty_land_grab':
                self.objective_empty_land_grab()
        self.give(0.25)
        self.objective_pursue_leftovers()
        return Plan(self)
