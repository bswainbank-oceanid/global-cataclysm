"""
A fast Monte Carlo estimate of how a battle would turn out, for the strategy bots' risk checks.

The real resolver (engine.combat.resolve_battle) narrates every roll as events, which costs about a
millisecond per battle -- too slow for the hundreds of evaluations a bot makes while planning a turn.
This re-plays the same rules without the narration, on plain lists:

  * the air superiority round (both sides have aircraft and a Fighter is among them), then rounds of
    attacker rolls, defender rolls, then both sides' casualties are removed together;
  * each unit rolls its own die (Fighters/Bombers with their air superiority adjustments), a "clean"
    target has defense <= the roll, otherwise only the next-lowest defense tier is in the pool and a
    maximum roll hits it for half damage; targets are weighted 2:1 toward the roller's own type
    (Bombers pick uniformly); Submarines and aircraft cannot see each other;
  * a unit that has nothing it could hit does not roll; the battle ends when a side is gone or neither
    side can hit the other;
  * XP and promotions after every round (survive +1, deal damage +1, kill a promoted unit +1; 5 XP per
    promotion, up to the rules' max_promotions), Dig In (defending Infantry +1 defense), the first-round bonus (amphibious
    landing / ambush / reclaim), and Transport form in sea battles.

"Success" is the attacker's -- all defenders eliminated and at least one attacker left, as if the
battle ran on without a round limit -- and tests/test_battle_sim.py checks the estimate against the
real resolver.
"""
import copy
import random

from ..combat import DIE_MAX

MAX_ROUNDS = 100


class BattleOdds:
    """attacker_wins / defender_wins / neither (a stalemate or mutual destruction), as fractions."""
    __slots__ = ('attacker_wins', 'defender_wins', 'neither', 'samples')

    def __init__(self, attacker_wins, defender_wins, neither, samples):
        self.attacker_wins = attacker_wins
        self.defender_wins = defender_wins
        self.neither = neither
        self.samples = samples


class _Side:
    """One side of a battle, prepared once: per-unit static facts, and the stats each kind of round
    uses (which change as units are promoted mid-battle -- see stats())."""

    def __init__(self, units, is_defender, bonus, battle_type, unit_defs, type_order, xp_required, max_promotions=None):
        self.units = units
        self.n = len(units)
        self.is_defender = is_defender
        self.bonus = bonus
        self.unit_defs = unit_defs
        self.type_order = type_order
        self.xp_required = xp_required
        self.max_promotions = max_promotions
        self.type = [u.unit_type for u in units]
        self.is_air = [unit_defs[u.unit_type]['category'] == 'Air' for u in units]
        self.is_sub = [t == 'Submarine' for t in self.type]
        self.cargo = [battle_type == 'sea' and unit_defs[u.unit_type]['category'] == 'Land' for u in units]
        self.hp0 = [unit_defs['Transport']['hp'] if c else u.current_hp for u, c in zip(units, self.cargo)]
        self.xp0 = [0 if c else u.xp for u, c in zip(units, self.cargo)]
        self.promotions0 = [u.promotions for u in units]
        self._unit_cache = {}
        self._table_cache = {}

    def _unit_stats(self, i, extra, kind):
        key = (i, extra, kind)
        got = self._unit_cache.get(key)
        if got is None:
            u = copy.copy(self.units[i])
            u.promotions += extra
            u.in_transport_form = self.cargo[i]
            s = u.effective_stats(self.unit_defs, round1_bonus=(kind == 'r1' and self.bonus),
                                  defending=self.is_defender, air_superiority=(kind == 'air'))
            die = s['attack_die']
            got = (DIE_MAX[die] if die else 0, s['damage'] or 0, s['defense'] if s['defense'] is not None else 99)
            self._unit_cache[key] = got
        return got

    def table(self, kind, extras):
        """(die_max/damage per unit, defense per unit, roll order) for a round of `kind` given each unit's
        promotions earned in this battle so far."""
        key = (kind, extras)
        got = self._table_cache.get(key)
        if got is None:
            rolls, defs, ranks = [], [], []
            for i in range(self.n):
                die_max, damage, defense = self._unit_stats(i, extras[i], kind)
                rolls.append((die_max, damage))
                defs.append(defense)
                if die_max:
                    t = self.type[i]
                    ranks.append((self.type_order.index(t) if t in self.type_order else len(self.type_order), die_max, i))
            ranks.sort()
            got = (rolls, defs, [i for _, _, i in ranks])
            self._table_cache[key] = got
        return got


def _roll_side(rng, side, enemies, my_table, enemy_defs, enemy_hp, alive, enemy_promoted):
    """One side's roll-through. Returns (damage per enemy index, indices of my units that dealt damage,
    [(killer index, victim index)] for kills of promoted victims). Damage is applied by the caller once
    both sides have rolled."""
    rolls, _, order = my_table
    left = list(enemy_hp)
    standing = [j for j in range(enemies.n) if left[j] > 0]
    pending = {}
    dealt = set()
    kills = []
    e_is_air, e_is_sub, e_type, e_cargo = enemies.is_air, enemies.is_sub, enemies.type, enemies.cargo
    rand = rng.random
    for i in order:
        if not standing:
            break
        if not alive[i]:
            continue
        if side.is_sub[i]:
            visible = [j for j in standing if not e_is_air[j]]
        elif side.is_air[i]:
            visible = [j for j in standing if not e_is_sub[j]]
        else:
            visible = standing
        if not visible:
            continue
        die_max, damage = rolls[i]
        roll = 1 + int(rand() * die_max)
        clean = [j for j in visible if enemy_defs[j] <= roll]
        if clean:
            pool, bypass = clean, False
        else:
            if roll != die_max:
                continue  # a miss
            low = min(enemy_defs[j] for j in visible)
            pool, bypass = [j for j in visible if enemy_defs[j] == low], True
        if len(pool) == 1:
            target = pool[0]
        elif side.type[i] == 'Bomber':
            target = pool[int(rand() * len(pool))]
        else:
            my_type = side.type[i]
            weights = [2 if e_type[j] == my_type else 1 for j in pool]
            pick = rand() * sum(weights)
            for target, w in zip(pool, weights):
                pick -= w
                if pick < 0:
                    break
        dmg = damage // 2 if bypass else damage
        if dmg <= 0:
            continue
        before = left[target]
        left[target] = before - dmg
        pending[target] = pending.get(target, 0) + dmg
        dealt.add(i)
        if left[target] <= 0:
            standing.remove(target)
            if before > 0 and enemy_promoted[target] and not e_cargo[target]:
                kills.append((i, target))
    return pending, dealt, kills


def _can_hit(side, enemies, alive, enemy_alive, order):
    for i in order:
        if not alive[i]:
            continue
        for j in range(enemies.n):
            if not enemy_alive[j]:
                continue
            if side.is_sub[i] and enemies.is_air[j]:
                continue
            if side.is_air[i] and enemies.is_sub[j]:
                continue
            return True
    return False


def _award(side, hp, alive_before_mask, dealt, kill_credits, xp, extras, promoted_flag):
    """XP and promotions after a round, as engine.combat._apply_xp_and_check_promotions does them."""
    cap = side.max_promotions

    def capped(i):
        return cap is not None and side.promotions0[i] + extras[i] >= cap

    for i in range(side.n):
        if hp[i] <= 0 or side.cargo[i] or not alive_before_mask[i] or capped(i):
            continue
        xp[i] += 1
        if i in dealt:
            xp[i] += 1
    for killer in kill_credits:
        if hp[killer] > 0 and not side.cargo[killer] and not capped(killer):
            xp[killer] += 1
    changed = False
    for i in range(side.n):
        if hp[i] <= 0 or side.cargo[i] or not alive_before_mask[i]:
            continue
        while xp[i] >= side.xp_required and not capped(i):
            xp[i] -= side.xp_required
            extras[i] += 1
            hp[i] += 1
            promoted_flag[i] = True
            changed = True
    return changed


def simulate_once(rng, att, dfn, air_round):
    """One battle. Returns 'attacker', 'defender' or 'neither'."""
    ahp, dhp = list(att.hp0), list(dfn.hp0)
    axp, dxp = list(att.xp0), list(dfn.xp0)
    aex, dex = [0] * att.n, [0] * dfn.n
    apro = [p > 0 for p in att.promotions0]
    dpro = [p > 0 for p in dfn.promotions0]

    atabs, dtabs = {}, {}  # each side's current tables per kind of round; rebuilt only after a promotion

    def apply(pa, pd):
        for j, dmg in pa.items():
            dhp[j] -= dmg
        for i, dmg in pd.items():
            ahp[i] -= dmg

    def tables(kind):
        at = atabs.get(kind)
        if at is None:
            at = atabs[kind] = att.table(kind, tuple(aex))
        dt = dtabs.get(kind)
        if dt is None:
            dt = dtabs[kind] = dfn.table(kind, tuple(dex))
        return at, dt

    if air_round:
        aa = [att.is_air[i] and ahp[i] > 0 for i in range(att.n)]
        da = [dfn.is_air[j] and dhp[j] > 0 for j in range(dfn.n)]
        at, dt = tables('air')
        ahp_view = [h if att.is_air[i] else 0 for i, h in enumerate(ahp)]  # only aircraft fight, and can be hit
        dhp_view = [h if dfn.is_air[j] else 0 for j, h in enumerate(dhp)]
        pa, adealt, akills = _roll_side(rng, att, dfn, at, dt[1], dhp_view, aa, dpro)
        pd, ddealt, dkills = _roll_side(rng, dfn, att, dt, at[1], ahp_view, da, apro)
        apply(pa, pd)
        if _award(att, ahp, aa, adealt, [k for k, _ in akills], axp, aex, apro):
            atabs.clear()
        if _award(dfn, dhp, da, ddealt, [k for k, _ in dkills], dxp, dex, dpro):
            dtabs.clear()

    for rnd in range(1, MAX_ROUNDS + 1):
        a_alive = [h > 0 for h in ahp]
        d_alive = [h > 0 for h in dhp]
        if not any(a_alive) or not any(d_alive):
            break
        kind = 'r1' if rnd == 1 else 'n'
        at, dt = tables(kind)
        if not _can_hit(att, dfn, a_alive, d_alive, at[2]) and not _can_hit(dfn, att, d_alive, a_alive, dt[2]):
            break
        pa, adealt, akills = _roll_side(rng, att, dfn, at, dt[1], dhp, a_alive, dpro)
        pd, ddealt, dkills = _roll_side(rng, dfn, att, dt, at[1], ahp, d_alive, apro)
        apply(pa, pd)
        if _award(att, ahp, a_alive, adealt, [k for k, _ in akills], axp, aex, apro):
            atabs.clear()
        if _award(dfn, dhp, d_alive, ddealt, [k for k, _ in dkills], dxp, dex, dpro):
            dtabs.clear()

    a_left, d_left = any(h > 0 for h in ahp), any(h > 0 for h in dhp)
    if a_left and not d_left:
        return 'attacker'
    if d_left and not a_left:
        return 'defender'
    return 'neither'


BATCH = 25


def estimate(attackers, defenders, battle_type, unit_defs, rules, rng=None, samples=200, round1_bonus_side=None,
             cut=None):
    """Odds for a battle between `attackers` and `defenders` (lists of UnitInstance; not modified).
    round1_bonus_side: None | 'attacker' | 'defender', as GameEngine.round1_bonus reports it.
    cut: attacker-win probabilities the caller is going to compare the answer with; sampling stops early
    once the estimate is clearly on one side of all of them (about three standard errors), which saves most
    of the work for lopsided battles."""
    rng = rng or random.Random()
    if not attackers:
        return BattleOdds(0.0, 1.0 if defenders else 0.0, 0.0 if defenders else 1.0, 0)
    if not defenders:
        return BattleOdds(1.0, 0.0, 0.0, 0)
    type_order = rules['combat']['resolution_order'][battle_type]
    xp_required = rules['promotion']['xp_required']
    max_promotions = rules['promotion'].get('max_promotions')
    att = _Side(list(attackers), False, round1_bonus_side == 'attacker', battle_type, unit_defs, type_order, xp_required, max_promotions)
    dfn = _Side(list(defenders), True, round1_bonus_side == 'defender', battle_type, unit_defs, type_order, xp_required, max_promotions)
    air_round = any(att.is_air) and any(dfn.is_air) and ('Fighter' in att.type or 'Fighter' in dfn.type)
    counts = {'attacker': 0, 'defender': 0, 'neither': 0}
    done = 0
    while done < samples:
        for _ in range(min(BATCH, samples - done)):
            counts[simulate_once(rng, att, dfn, air_round)] += 1
            done += 1
        if cut and done >= 2 * BATCH and done < samples:
            p = counts['attacker'] / done
            margin = 3.0 * (max(p * (1 - p), 0.01) / done) ** 0.5 + 0.02
            if all(abs(p - c) > margin for c in cut):
                break
    return BattleOdds(counts['attacker'] / done, counts['defender'] / done, counts['neither'] / done, done)
