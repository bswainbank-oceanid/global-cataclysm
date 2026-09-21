"""The strategy bots: their settings and weighted draws, the planner's objectives, and whole turns."""
import collections
import json
import os
import random
import unittest

from engine import data
from engine.bots import strategy_settings
from engine.bots.planner import Planner
from engine.bots.random_bot import RandomBot
from engine.bots.strategy_bot import StrategyBot
from engine.bots.strategy_settings import (
    ALL_STYLES, SECONDARY, STYLES, load_settings, weighted_choice, weighted_order,
)
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode, Phase, UnitInstance

FACTIONS = ('NAA', 'AAC', 'UE', 'GPC', 'PAF', 'UER')
UNITS = ('Infantry', 'Mechanized Infantry', 'Armor', 'Aircraft Carrier', 'Cruiser', 'Submarine', 'Bomber', 'Fighter')


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.s = load_settings()

    def test_the_sheet_was_read_completely(self):
        self.assertEqual(sorted(self.s.unit_weights), sorted(FACTIONS))
        for f in FACTIONS:
            self.assertEqual(sorted(self.s.unit_weights[f]), sorted(UNITS))
            self.assertEqual(sorted(self.s.strategy_weights[f]), sorted(ALL_STYLES))
        self.assertEqual(sorted(self.s.thresholds), sorted(STYLES))
        for style in STYLES:
            for name in ('hold_sc', 'capture_sc', 'reinforce_contested', 'punish_betrayers') + SECONDARY:
                self.assertIn(name, self.s.thresholds[style])
        self.assertEqual(self.s.distance_costs, {'friendly': 2, 'sea': 3, 'enemy': 5, 'allied': 3})

    def test_a_few_known_cells(self):
        self.assertEqual(self.s.unit_weights['NAA']['Mechanized Infantry'], 6)
        self.assertEqual(self.s.strategy_weights['GPC']['Controlling'], 7)
        self.assertEqual(self.s.thresholds['Strategic']['hold_sc'], {'min': 0.001, 'max': 75})
        self.assertEqual(self.s.thresholds['Controlling']['control_oceans'], {'weight': 10, 'min': 65, 'max': 95})
        self.assertEqual(self.s.limits('Defensive', 'hold_sc'), (0.00001, 0.75))
        self.assertEqual(self.s.thresholds['Expansive']['empty_land_grab'], {'weight': 10})
        self.assertIn('empty_land_grab', SECONDARY)

    def test_a_weight_of_zero_is_never_drawn(self):
        rng = random.Random(1)
        self.assertEqual({weighted_choice(rng, ['a', 'b', 'c'], [0, 5, 0]) for _ in range(200)}, {'b'})
        self.assertIsNone(weighted_choice(rng, ['a', 'b'], [0, 0]))
        for _ in range(50):
            self.assertNotIn('c', weighted_order(rng, ['a', 'b', 'c'], [3, 1, 0]))

    def test_draws_follow_the_odds(self):
        rng = random.Random(2)
        counts = collections.Counter(weighted_choice(rng, ['a', 'b'], [3, 1]) for _ in range(4000))
        self.assertAlmostEqual(counts['a'] / 4000, 0.75, delta=0.03)

    def test_a_style_is_drawn_by_the_factions_row(self):
        rng = random.Random(3)
        counts = collections.Counter(self.s.draw_style('NAA', rng) for _ in range(3000))
        total = sum(self.s.strategy_weights['NAA'].values())
        for style in ALL_STYLES:
            self.assertAlmostEqual(counts[style] / 3000, self.s.strategy_weights['NAA'][style] / total, delta=0.04)

    def test_variable_rerolls_among_the_four_concrete_styles_by_the_same_row(self):
        rng = random.Random(4)
        seen = collections.Counter(self.s.turn_style('AAC', 'Variable', rng) for _ in range(2000))
        self.assertEqual(set(seen), set(STYLES))
        self.assertEqual({self.s.turn_style('AAC', 'Defensive', rng) for _ in range(20)}, {'Defensive'})

    def test_secondary_objectives_are_ordered_by_weight_and_zero_weights_are_skipped(self):
        rng = random.Random(5)
        firsts = collections.Counter(self.s.secondary_order('Strategic', rng)[0] for _ in range(3000))
        self.assertEqual(max(firsts, key=firsts.get), 'pursue_sc_1')  # weight 10 of 28
        for _ in range(100):
            order = self.s.secondary_order('Defensive', rng)
            self.assertNotIn('pursue_sc_2', order)  # weight 0 for Defensive
            self.assertNotIn('pursue_sc_3', order)
            self.assertEqual(len(order), len(set(order)))

    def test_purchase_odds_come_from_the_factions_unit_weights(self):
        odds = self.s.unit_odds('PAF', ['Infantry', 'Aircraft Carrier', 'Fighter'])
        self.assertEqual(odds, [9, 2, 2])

    def test_the_json_matches_the_spreadsheet_builder(self):
        path = os.path.join(os.path.dirname(strategy_settings.__file__), '..', '..', 'data', 'bot_settings.json')
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        self.assertEqual(raw['unit_weights']['UER']['Infantry'], 8)


def make_game(modes=None, seed=1, first_turn_combat=True):
    modes = modes or {f: FactionMode.NEUTRAL for f in FACTIONS}
    modes = dict(modes)
    rng = random.Random(seed)
    gs = build_game_state('starting_setup_125ipc', {f: modes.get(f, FactionMode.NEUTRAL) for f in FACTIONS},
                          randomize_play_order=False, allow_combat_moves_first_turn=first_turn_combat, rng=rng)
    engine = GameEngine(gs, data, combat_rng=random.Random(seed + 1))
    return engine, gs


def put(gs, tid, unit_type, owner, n=1, promotions=0):
    for _ in range(n):
        gs.territories[tid].units.append(UnitInstance(
            unit_id=gs.new_unit_id(),
            unit_type=unit_type, owner=owner, current_hp=data.units()[unit_type]['hp'] + promotions, promotions=promotions))


def planner_for(engine, faction='NAA', style='Strategic', mode='full', budget=1500, seed=7):
    return Planner(engine, faction, load_settings(), style, random.Random(seed), mode, budget)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        modes = {f: FactionMode.NEUTRAL for f in FACTIONS}
        modes['NAA'] = modes['GPC'] = FactionMode.BOT
        self.engine, self.gs = make_game(modes)
        # England (21), NAA's, with Scotland (13) next to it: GPC masses in Scotland.
        self.gs.territories[21].units = [u for u in self.gs.territories[21].units if u.unit_type == 'Bomber'][:1]
        put(self.gs, 13, 'Armor', 'GPC', 3)
        self.gs.territories[13].owner = 'NAA'

    def test_no_threat_means_nothing_to_do(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT})
        p = planner_for(engine)
        self.assertEqual(p.hold_chance(21, p.defenders_at(21, claimed_only=False)), 1.0)

    def test_a_threatened_strategic_center_is_reinforced_by_purchase(self):
        p = planner_for(self.engine)
        weak = p.hold_chance(21, p.defenders_at(21, claimed_only=False))
        self.assertLess(weak, 0.3)
        p.secure(21, 'hold_sc', (0.2, 0.9))
        stronger = p.hold_chance(21, p.defenders_at(21), fast=False)
        self.assertGreater(stronger, 0.7)
        self.assertGreater(stronger, weak)
        self.assertTrue(p.purchases or p.moves_nc or p.stay)
        self.assertTrue(all(tid == 21 for (_, tid) in p.purchases))
        self.engine._resolve_and_cost(p.purchase_orders(), 'NAA')  # everything bought is legal

    def test_an_objective_that_cannot_reach_its_min_commits_nothing(self):
        put(self.gs, 13, 'Armor', 'GPC', 40, promotions=3)  # hopeless
        p = planner_for(self.engine)
        ok = p.secure(21, 'hold_sc', (0.5, 0.9))
        self.assertFalse(ok)
        self.assertFalse(p.purchases or p.moves_nc or p.stay or p.claimed)
        self.assertEqual(p.treasury, self.gs.factions['NAA'].treasury_mpc)

    def test_the_treasury_is_never_overspent(self):
        p = planner_for(self.engine)
        p.run()
        cost, _ = self.engine._resolve_and_cost(p.purchase_orders(), 'NAA')
        self.assertLessEqual(cost, self.gs.factions['NAA'].treasury_mpc)

    def test_a_unit_serves_only_one_objective(self):
        p = planner_for(self.engine)
        plan = p.run()
        moved = list(p.moves_combat) + list(p.moves_nc)
        self.assertEqual(len(moved), len(set(moved)))
        for uid in moved:
            self.assertIn(uid, p.claimed)

    def test_empty_territory_an_enemy_walk_in_gets_a_garrison(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gs.territories[13].units = []
        put(gs, 13, 'Infantry', 'GPC', 1)  # an enemy Infantry stands next to empty... make England reachable
        gs.territories[13].owner = 'NAA'
        gs.territories[21].units = []
        p = planner_for(engine)
        p.objective_fill_gaps()
        self.assertTrue(p.purchases or p.moves_nc, 'England should get a garrison')

    def test_a_weak_strategic_center_is_attacked_by_a_strong_force(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT})
        # give NAA overwhelming force at England and make Scotland a lone-defender GPC SC... use an existing GPC SC
        gpc_sc = next(t for t, x in data.territories().items() if x['type'] == 'land' and x.get('faction') == 'GPC' and x.get('strategic_center'))
        neighbours = data.adjacency()[gpc_sc]
        land_neighbour = next(n for n in neighbours if data.territories()[n]['type'] == 'land')
        gs.territories[land_neighbour].owner = 'NAA'
        put(gs, land_neighbour, 'Armor', 'NAA', 8, promotions=1)
        gs.territories[gpc_sc].units = []
        put(gs, gpc_sc, 'Infantry', 'GPC', 1)
        p = planner_for(engine)
        p.objective_capture_scs()
        self.assertTrue(p.moves_combat, 'the strong force should go for the weak Strategic Center')
        for uid, path in p.moves_combat.items():
            self.assertEqual(path[-1], gpc_sc)

    def test_an_eliminated_factions_empty_strategic_center_is_still_a_target(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gpc_sc = next(t for t, x in data.territories().items() if x['type'] == 'land' and x.get('faction') == 'GPC' and x.get('strategic_center'))
        neighbours = data.adjacency()[gpc_sc]
        land_neighbour = next(n for n in neighbours if data.territories()[n]['type'] == 'land')
        gs.territories[land_neighbour].owner = 'NAA'
        put(gs, land_neighbour, 'Infantry', 'NAA', 1)
        gs.territories[gpc_sc].units = []
        gs.factions['GPC'].eliminated = True   # surrendered: its land stays, empty
        p = planner_for(engine)
        self.assertTrue(p.capturable('GPC'))
        self.assertFalse(p.hostile('GPC'))
        self.assertIn(gpc_sc, [t[1] for t in p.sc_targets()])
        p.objective_capture_scs()
        self.assertEqual([path[-1] for path in p.moves_combat.values()], [gpc_sc])

    def test_a_neutral_factions_land_is_never_a_target(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT})
        p = planner_for(engine)
        self.assertFalse(p.capturable('GPC'))
        self.assertFalse(p.capturable('NAA'))

    def test_the_min_risk_of_an_sc_attack_is_the_chance_to_force_a_contest(self):
        engine, gs = make_game({**{f: FactionMode.NEUTRAL for f in FACTIONS}, 'NAA': FactionMode.BOT, 'GPC': FactionMode.BOT})
        gpc_sc = next(t for t, x in data.territories().items() if x['type'] == 'land' and x.get('faction') == 'GPC' and x.get('strategic_center'))
        land_neighbour = next(n for n in data.adjacency()[gpc_sc] if data.territories()[n]['type'] == 'land')
        gs.territories[land_neighbour].owner = 'NAA'
        put(gs, land_neighbour, 'Infantry', 'NAA', 6)   # far too weak to win outright...
        gs.territories[gpc_sc].units = []
        put(gs, gpc_sc, 'Armor', 'GPC', 3, promotions=1)
        p = planner_for(engine)
        win = p.assess_assault(gpc_sc, ('Land', 'Air', 'Sea'))
        held = p.assess_assault(gpc_sc, ('Land', 'Air', 'Sea'), contest=True)
        self.assertLess(win, 0.15)
        self.assertGreater(held, 0.3)   # ...but they can often hang on through the three rounds
        # a min of 0% risk of a contest: the attack goes in even though victory is unlikely
        p2 = planner_for(engine)
        self.assertTrue(p2.assault(gpc_sc, 'capture_sc', (held - 0.2, 0.95), ('Land', 'Air', 'Sea'), min_is_contest=True))
        self.assertTrue(p2.moves_combat)
        # with the min above what a contest can be had for, it stays home
        p3 = planner_for(engine)
        self.assertFalse(p3.assault(gpc_sc, 'capture_sc', (min(held + 0.25, 0.99), 0.95), ('Land', 'Air', 'Sea'), min_is_contest=True))
        self.assertFalse(p3.moves_combat)

    def test_planning_effort_is_a_budget_of_simulated_battles(self):
        p = planner_for(self.engine, budget=40)
        p.run()
        self.assertLessEqual(p.sims_used, 40 + 300)  # (one evaluation can overshoot a little)

    def test_the_same_seed_plans_the_same_turn(self):
        a = planner_for(self.engine, seed=11).run()
        b = planner_for(self.engine, seed=11).run()
        self.assertEqual([(o.unit_type, o.qty, o.deploy_at) for o in a.purchases], [(o.unit_type, o.qty, o.deploy_at) for o in b.purchases])
        self.assertEqual([(o.unit_id, o.path) for o in a.combat], [(o.unit_id, o.path) for o in b.combat])



class TestEmptyLandGrab(unittest.TestCase):
    """The secondary objective: Mechanized Infantry take undefended enemy land."""

    def setUp(self):
        modes = {f: FactionMode.NEUTRAL for f in FACTIONS}
        modes['NAA'] = modes['GPC'] = FactionMode.BOT
        self.engine, self.gs = make_game(modes)
        # Scotland (13) is NAA's next to England (21); GPC's garrison there is gone: an empty territory.
        self.gs.territories[13].owner = 'GPC'
        self.gs.territories[13].units = []
        self.mech = None

    def with_mech(self, at=21):
        put(self.gs, at, 'Mechanized Infantry', 'NAA')
        self.mech = self.gs.territories[at].units[-1]

    def test_the_weight_is_a_secondary_objective_like_the_others(self):
        for style in ALL_STYLES:
            self.assertGreater(load_settings().thresholds['Strategic' if style == 'Variable' else style]['empty_land_grab']['weight'], 0)

    def test_undefended_land_is_found_nearest_first(self):
        p = planner_for(self.engine)
        found = p._undefended_land()
        self.assertIn(13, [t for _, _, t in found])
        hops = [h for h, _, _ in found]
        self.assertEqual(hops, sorted(hops))
        # a defended one is not there
        put(self.gs, 13, 'Infantry', 'GPC')
        self.assertNotIn(13, [t for _, _, t in planner_for(self.engine)._undefended_land()])

    def test_an_eliminated_factions_land_counts_as_undefended_too(self):
        self.gs.territories[13].owner = 'UER'
        self.gs.factions['UER'].eliminated = True
        self.gs.factions['UER'].mode = FactionMode.BOT
        self.assertIn(13, [t for _, _, t in planner_for(self.engine)._undefended_land()])

    def test_a_mech_inf_in_reach_goes_to_take_it(self):
        self.with_mech()
        p = planner_for(self.engine)
        p.objective_empty_land_grab()
        self.assertEqual(p.moves_combat[self.mech.unit_id][-1], 13)
        self.assertEqual(p.claimed[self.mech.unit_id], 'empty_land_grab')
        self.gs.phase = Phase.COMBAT_MOVE
        self.gs.active_faction = 'NAA'
        self.engine.submit_combat_moves('NAA', p.combat_orders())  # and it is a legal move

    def test_only_mechanized_infantry_are_sent(self):
        put(self.gs, 21, 'Infantry', 'NAA', 2)
        put(self.gs, 21, 'Armor', 'NAA', 1)
        p = planner_for(self.engine)
        p.objective_empty_land_grab()
        self.assertEqual({tid for tid in p.moves_combat}, set())

    def test_with_no_mech_inf_in_reach_one_is_bought_toward_it(self):
        p = planner_for(self.engine)
        p.objective_empty_land_grab()
        self.assertTrue(p.purchases)
        self.assertEqual({t for (t, _) in p.purchases}, {'Mechanized Infantry'})
        self.engine._resolve_and_cost(p.purchase_orders(), 'NAA')   # everything bought is legal

    def test_the_last_defender_of_a_threatened_territory_is_not_sent(self):
        self.gs.territories[21].units = []
        self.with_mech()
        put(self.gs, 13, 'Armor', 'GPC', 1)  # not empty any more...
        self.gs.territories[13].units = []   # (keep it empty, but let a GPC land unit threaten England from next door)
        put(self.gs, 12, 'Armor', 'GPC', 1)
        self.gs.territories[12].owner = 'GPC'
        p = planner_for(self.engine)
        p.objective_empty_land_grab()
        # England's only defender is the Mech Inf and an enemy Armor can walk in: it stays (a purchase stands in instead)
        if self.mech.unit_id in p.moves_combat:
            self.assertTrue(p.threats(21) == {} or any(v.unit_id != self.mech.unit_id for v in p.defenders_at(21, claimed_only=False)))

class TestStrategyBotPlaysTurns(unittest.TestCase):
    def game(self, seed=3, budget=250):
        modes = {f: FactionMode.BOT for f in FACTIONS}
        rng = random.Random(seed)
        gs = build_game_state('starting_setup_125ipc', modes, rng=rng)
        engine = GameEngine(gs, data, combat_rng=random.Random(rng.random()))
        bots = {f: StrategyBot(engine, f, rng=random.Random(rng.random()), budget=budget) for f in modes}
        return engine, gs, bots

    def test_every_faction_gets_a_style_by_its_weights(self):
        engine, gs, bots = self.game()
        for bot in bots.values():
            self.assertIn(bot.base_style, ALL_STYLES)

    def test_a_dozen_turns_play_without_an_illegal_order(self):
        from engine.bots.driver import play_to_completion
        engine, gs, bots = self.game()
        turns = play_to_completion(engine, bots, max_turns=12)
        self.assertEqual(turns, 12)
        for f, fs in gs.factions.items():
            self.assertGreaterEqual(fs.treasury_mpc, 0)

    def test_the_bots_buy_and_move(self):
        from engine.bots.driver import play_to_completion
        from engine.turn_log import TurnLog
        engine, gs, bots = self.game(seed=4)
        log = TurnLog()
        engine.turn_log = log
        play_to_completion(engine, bots, max_turns=12)
        kinds = collections.Counter(e['kind'] for e in log.events)
        self.assertGreater(kinds['purchase'], 0)
        self.assertGreater(kinds['unit_deployed'], 0)
        self.assertGreater(kinds['noncombat_move'], 0)

    def test_a_watcher_asking_twice_gets_the_same_purchase_plan(self):
        engine, gs, bots = self.game()
        bot = bots[gs.active_faction]
        bot.plan_purchase_phase()
        first = list(engine._staged_purchases[bot.faction])
        bot.plan_purchase_phase()
        self.assertEqual(first, list(engine._staged_purchases[bot.faction]))

    def test_the_random_bot_is_still_available(self):
        engine, gs, _ = self.game()
        bot = RandomBot(engine, gs.active_faction, rng=random.Random(1))
        bot.plan_purchase_phase()


if __name__ == '__main__':
    unittest.main()
