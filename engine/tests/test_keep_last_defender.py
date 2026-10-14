"""Planner option keep_last_defender (on in the alternate strategy profiles): no objective takes the last unit out of
a threatened territory of mine -- an enemy land unit could reach it, or enemies are in it -- unless something
replaces it (a purchase there, or another of my units moving in)."""
import random
import unittest

from engine.bots.planner import Planner
from engine.bots.strategy_settings import load_settings
from server.lobby import build_session
from server.tests.test_persist import bot, seat


def border_board(seed):
    """A real starting board with a front line drawn on purpose: several of my border territories hold one Infantry
    each, with an enemy Infantry next door -- lone defenders of threatened territories. Returns (session, planner
    maker, [those territories])."""
    from engine.tests.test_engine import make_unit
    session, _ = build_session({'seats': [bot('strategy'), bot('strategy')] + [seat('NONCOMBATANT')] * 4, 'seed': seed})
    engine, gs = session.engine, session.engine.game_state
    me = gs.active_faction
    enemy = next(f for f in session.bots if f != me)
    terrs, adj = engine.data.territories(), engine.data.adjacency()
    fronts = []
    for tid in sorted(gs.territories):
        t = gs.territories[tid]
        if terrs[tid]['type'] != 'land' or t.owner != me or gs.is_strategic_center(tid, terrs[tid]):
            continue
        nxt = next((n for n in adj[tid] if terrs[n]['type'] == 'land' and gs.territories[n].owner not in (me,)
                    and not gs.is_strategic_center(n, terrs[n])), None)
        if nxt is None or nxt in fronts:
            continue
        t.units = [make_unit('Infantry', me)]
        gs.territories[nxt].owner = enemy
        gs.territories[nxt].units = [make_unit('Infantry', enemy)]
        fronts.append(tid)
        if len(fronts) == 6:
            break

    def planner(bot_set):
        return Planner(engine, me, load_settings(engine.data, bot_set), 'Expansive', random.Random(seed), 'full', 1500)
    return session, planner, fronts


def stripped(p):
    """Threatened territories of mine that had a unit of mine and that the plan leaves with no defender at all."""
    out = []
    for tid, t in p.gs.territories.items():
        if not p.is_land(tid) or t.owner != p.me or not any(u.owner == p.me for u in t.units):
            continue
        threatened = p.enemies_at(tid) or any(p.category(v) == 'Land' for units in p.threats(tid).values() for v in units)
        if threatened and not p.defenders_at(tid, claimed_only=False):
            out.append(tid)
    return out


class TestKeepLastDefender(unittest.TestCase):
    def test_no_threatened_territory_is_left_empty_by_the_alternate_set(self):
        for seed in (61, 62):
            _, planner, fronts = border_board(seed)
            self.assertTrue(fronts)
            p = planner('alternate')
            p.run()
            self.assertEqual([t for t in stripped(p) if t in fronts], [], f'seed {seed}')

    def test_the_last_defender_is_not_free_but_a_replacement_frees_it(self):
        _, planner, fronts = border_board(61)
        p = planner('alternate')
        tid = fronts[0]
        u = next(u for u in p.gs.territories[tid].units if u.owner == p.me)
        self.assertTrue(p._threatened(tid))
        self.assertFalse(p.free_for(u, 'nc'))
        self.assertFalse(p.free_for(u, 'combat'))
        p.stubs.setdefault(tid, []).append(p._stub('Infantry'))  # (an Infantry bought for it)
        self.assertTrue(p.free_for(u, 'nc'))
        q = planner('standard')  # (the standard set: no such rule)
        self.assertTrue(q.free_for(next(v for v in q.gs.territories[tid].units if v.owner == q.me), 'nc'))


if __name__ == '__main__':
    unittest.main()
