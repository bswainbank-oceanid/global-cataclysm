import unittest

from engine import data as real_data
from engine.bots.driver import play_to_completion
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import Phase, FactionMode
from engine.stats import GameStats
from engine.tests.test_engine import FakeData, ScriptedRNG, make_state, make_unit


class FakeDataWithExcludedZones(FakeData):
    """FakeData, but with a controllable setup.excluded_naval_zones
    instead of always delegating to the real ruleset -- lets a test pick
    a small, deliberate exclusion list rather than the real one (43)."""
    def __init__(self, *args, excluded_naval_zones, **kwargs):
        super().__init__(*args, **kwargs)
        self._excluded_naval_zones = excluded_naval_zones

    def rules(self):
        return {'setup': {'excluded_naval_zones': self._excluded_naval_zones}}


class TestPurchaseTargetPools(unittest.TestCase):
    def test_excluded_naval_zone_is_never_a_purchase_target(self):
        # 1: land SC (NAA), 2: land non-SC (NAA), 3: sea adjacent to
        # both (would otherwise be an SC target, per the other tests
        # here) -- excluded outright by policy.excluded_naval_purchase_zones.
        data = FakeDataWithExcludedZones(
            territories={
                1: {'type': 'land', 'value': 2, 'strategic_center': True},
                2: {'type': 'land', 'value': 3},
                3: {'type': 'sea'},
            },
            adjacency={1: [3], 2: [3], 3: [1, 2]},
            excluded_naval_zones=[3],
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        sc_targets, other_targets = bot._purchase_target_pools()
        self.assertEqual(set(sc_targets), {1})
        self.assertEqual(set(other_targets), {2})
        self.assertNotIn(3, sc_targets)
        self.assertNotIn(3, other_targets)

    def test_sc_adjacent_sea_zone_is_classified_as_an_sc_target(self):
        # 1: land SC (NAA), 2: land non-SC (NAA), 3: sea adjacent to both.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 2, 'strategic_center': True},
                2: {'type': 'land', 'value': 3},
                3: {'type': 'sea'},
            },
            adjacency={1: [3], 2: [3], 3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        sc_targets, other_targets = bot._purchase_target_pools()
        self.assertEqual(set(sc_targets), {1, 3})
        self.assertEqual(set(other_targets), {2})

    def test_sea_zone_with_no_adjacent_sc_is_not_an_sc_target(self):
        data = FakeData(
            territories={2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={2: [3], 3: [2]},
        )
        gs = make_state(data, {2: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        sc_targets, other_targets = bot._purchase_target_pools()
        self.assertEqual(sc_targets, [])
        self.assertEqual(set(other_targets), {2, 3})


class TestPurchasePhase(unittest.TestCase):
    def test_spends_within_treasury_and_deploys_pending_units(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, treasury={'NAA': 20})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA', rng=__import__('random').Random(1))
        bot.take_purchase_phase()

        self.assertGreaterEqual(gs.factions['NAA'].treasury_mpc, 0)
        self.assertLessEqual(gs.factions['NAA'].treasury_mpc, 20)
        pending_cost = sum(
            data.units()[u.unit_type]['sc_cost'] for u in gs.territories[1].pending_deployment
        )
        self.assertEqual(pending_cost, 20 - gs.factions['NAA'].treasury_mpc)

    def test_never_purchases_land_units_for_a_sea_target(self):
        # Bot defense policy, this session: "SCs should never produce
        # land units in sea areas" -- applies to every sea target, not
        # just an SC-funded one, since a land unit has nothing to
        # independently exist on in open water either way. A generous
        # treasury and only 2 targets total (1 land, 2 sea) makes it
        # near-certain some purchases land at the sea target, which is
        # what this test actually needs to be meaningful.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}, 2: {'type': 'sea'}},
            adjacency={1: [2], 2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, treasury={'NAA': 200})
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA', rng=__import__('random').Random(1))
        bot.take_purchase_phase()

        sea_pending = gs.territories[2].pending_deployment
        self.assertTrue(sea_pending, 'expected at least one purchase at the sea target for this test to be meaningful')
        unit_defs = data.units()
        for u in sea_pending:
            self.assertNotEqual(
                unit_defs[u.unit_type]['category'], 'Land',
                f'{u.unit_type} is a land unit but was purchased at sea territory 2',
            )


class TestCombatMovePhase(unittest.TestCase):
    def test_prefers_lowest_id_among_equally_close_targets(self):
        # 2 (NAA) adjacent to 4 and 7, both empty AAC-owned land, 1 hop away.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                4: {'type': 'land', 'value': 1},
                7: {'type': 'land', 'value': 1},
            },
            adjacency={2: [4, 7], 4: [2], 7: [2]},
        )
        gs = make_state(data, {2: 'NAA', 4: 'AAC', 7: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
                        phase=Phase.COMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[4].units])
        self.assertTrue(unit.has_moved_combat)

    def test_transported_land_unit_prefers_land_over_stopping_at_sea(self):
        # 2 (NAA infantry) -> 3 (sea, hostile: AAC Cruiser present) -> 6 (empty foreign land).
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                3: {'type': 'sea'},
                6: {'type': 'land', 'value': 1},
            },
            adjacency={2: [3], 3: [2, 6], 6: [3]},
        )
        gs = make_state(data, {2: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, phase=Phase.COMBAT_MOVE)
        mover = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(mover)
        gs.territories[3].units.append(make_unit('Cruiser', 'AAC'))
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(mover.unit_id, [u.unit_id for u in gs.territories[6].units])

    def test_amphibious_attack_preferred_over_a_merely_safe_landing(self):
        # 2 (NAA infantry) -> 3 (sea, hostile) -> either 5 (NAA's OWN
        # land -- a safe landing, i.e. a retreat) or 9 (AAC-occupied
        # land -- an actual attack). Both are equally close (2 hops) and
        # 5 has the lower id, so the plain closest/lowest-id tie-break
        # would normally pick 5 first -- the bot must never retreat to
        # safety when an attack option exists, so it picks 9 instead.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                3: {'type': 'sea'},
                5: {'type': 'land', 'value': 1},
                9: {'type': 'land', 'value': 1},
            },
            adjacency={2: [3], 3: [2, 5, 9], 5: [3], 9: [3]},
        )
        gs = make_state(
            data, {2: 'NAA', 5: 'NAA', 9: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
            phase=Phase.COMBAT_MOVE,
        )
        mover = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(mover)
        gs.territories[3].units.append(make_unit('Cruiser', 'AAC'))
        gs.territories[9].units.append(make_unit('Infantry', 'AAC'))
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(mover.unit_id, [u.unit_id for u in gs.territories[9].units], 'must press the attack, not retreat to friendly land')

    def test_falls_back_to_safe_landing_when_no_attack_option_exists(self):
        # Same shape, but this time the only land beyond the hostile
        # water is NAA's own (5) -- no attack option anywhere, so the
        # safe landing is the correct fallback, not a failure to move.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                3: {'type': 'sea'},
                5: {'type': 'land', 'value': 1},
            },
            adjacency={2: [3], 3: [2, 5], 5: [3]},
        )
        gs = make_state(
            data, {2: 'NAA', 5: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, phase=Phase.COMBAT_MOVE,
        )
        mover = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(mover)
        gs.territories[3].units.append(make_unit('Cruiser', 'AAC'))
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(mover.unit_id, [u.unit_id for u in gs.territories[5].units])

    def test_infantry_on_an_owned_sc_never_gets_a_combat_move_order(self):
        # Bot defense policy, this session: "Infantry should never leave
        # an SC." Territory 2 is a Strategic Center NAA owns, with an
        # adjacent empty foreign target (4) an Infantry there would
        # otherwise combat-move into as its "first legal move" -- it
        # must stay put instead. A second Infantry at NON-SC territory 1
        # (also adjacent to 4) confirms the garrison rule is scoped to
        # the SC specifically, not Infantry across the board.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 1},
                2: {'type': 'land', 'value': 2, 'strategic_center': True},
                4: {'type': 'land', 'value': 1},
            },
            adjacency={1: [4], 2: [4], 4: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA', 4: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
                        phase=Phase.COMBAT_MOVE)
        garrison = make_unit('Infantry', 'NAA')
        mobile = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(garrison)
        gs.territories[1].units.append(mobile)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(garrison.unit_id, [u.unit_id for u in gs.territories[2].units], 'must never leave the SC')
        self.assertFalse(garrison.has_moved_combat)
        self.assertTrue(mobile.has_moved_combat, 'a non-SC Infantry should still move normally')

    def test_armor_on_an_owned_sc_still_moves_normally(self):
        # The garrison rule is scoped to Infantry specifically (the
        # ruleset's designated defensive garrison unit type) -- Armor
        # sitting on the same SC is unaffected.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2, 'strategic_center': True},
                4: {'type': 'land', 'value': 1},
            },
            adjacency={2: [4], 4: [2]},
        )
        gs = make_state(data, {2: 'NAA', 4: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, phase=Phase.COMBAT_MOVE)
        mover = make_unit('Armor', 'NAA')
        gs.territories[2].units.append(mover)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_combat_move_phase()

        self.assertIn(mover.unit_id, [u.unit_id for u in gs.territories[4].units])


class TestNonCombatMovePhase(unittest.TestCase):
    def test_moves_toward_nearest_enemy_owned_territory(self):
        # 2 (NAA unit) -- 8 (NAA, empty) -- 9 (AAC-owned): only 8 is a
        # legal non-combat destination (9 is clean foreign land), and
        # it's the one that gets closer to 9.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2},
                8: {'type': 'land', 'value': 2},
                9: {'type': 'land', 'value': 1},
            },
            adjacency={2: [8], 8: [2, 9], 9: [8]},
        )
        gs = make_state(data, {2: 'NAA', 8: 'NAA', 9: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
                        phase=Phase.NONCOMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_noncombat_move_phase()

        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[8].units])
        self.assertTrue(unit.has_moved_noncombat)

    def test_unit_with_no_legal_moves_stays_put(self):
        data = FakeData(territories={2: {'type': 'land', 'value': 2}}, adjacency={2: []})
        gs = make_state(data, {2: 'NAA'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT}, phase=Phase.NONCOMBAT_MOVE)
        unit = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(unit)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_noncombat_move_phase()  # should not raise
        self.assertIn(unit.unit_id, [u.unit_id for u in gs.territories[2].units])

    def test_infantry_on_an_owned_sc_never_gets_a_noncombat_move_order(self):
        # Same shape as test_moves_toward_nearest_enemy_owned_territory
        # (territory 8 would otherwise be the correct move, getting
        # closer to enemy-owned 9), but the mover starts ON an owned SC
        # -- it must stay there instead.
        data = FakeData(
            territories={
                2: {'type': 'land', 'value': 2, 'strategic_center': True},
                8: {'type': 'land', 'value': 2},
                9: {'type': 'land', 'value': 1},
            },
            adjacency={2: [8], 8: [2, 9], 9: [8]},
        )
        gs = make_state(data, {2: 'NAA', 8: 'NAA', 9: 'AAC'}, {'NAA': FactionMode.BOT, 'AAC': FactionMode.BOT},
                        phase=Phase.NONCOMBAT_MOVE)
        garrison = make_unit('Infantry', 'NAA')
        gs.territories[2].units.append(garrison)
        engine = GameEngine(gs, data)
        bot = RandomBot(engine, 'NAA')
        bot.take_noncombat_move_phase()

        self.assertIn(garrison.unit_id, [u.unit_id for u in gs.territories[2].units])
        self.assertFalse(garrison.has_moved_noncombat)


class TestPlayToCompletion(unittest.TestCase):
    def test_naa_vs_aac_bot_game_runs_without_error(self):
        modes = {code: FactionMode.NEUTRAL for code in real_data.factions()}
        modes['NAA'] = FactionMode.BOT
        modes['AAC'] = FactionMode.BOT
        gs = build_game_state('starting_setup_200ipc', modes)
        stats = GameStats()
        engine = GameEngine(gs, stats=stats)
        bots = {
            'NAA': RandomBot(engine, 'NAA', rng=__import__('random').Random(1)),
            'AAC': RandomBot(engine, 'AAC', rng=__import__('random').Random(2)),
        }

        turns_played = play_to_completion(engine, bots, max_turns=15)

        self.assertGreater(turns_played, 0)
        report = stats.report()
        self.assertIn('=== Territory Captures ===', report)
        self.assertIn('=== Unit Stats ===', report)

    def test_first_turn_with_combat_moves_disabled_still_deploys_and_collects_income(self):
        # game_start_settings.allow_combat_moves_first_turn defaults
        # False -- advance_phase() skips Combat Move entirely on a
        # faction's own first turn. A real bug found this session: the
        # driver's old shape (one independent `if phase == X: ...;
        # advance_phase()` block per phase) double-advanced past
        # whichever phase came right after the skipped one, and that
        # cascaded all the way through -- Combat Resolution, Non-Combat
        # Move, Capture Territory, and Deploy + Income were ALL silently
        # skipped on every faction's first turn, every game, no matter
        # who the bots were. Purchased units sat in pending_deployment
        # forever and no income was ever collected. This proves a single
        # first turn, with the (default) setting active, still actually
        # reaches Deploy + Income and deploys whatever was purchased.
        # randomize_play_order=False and seeded bot RNGs make this
        # deterministic -- which faction goes first, and exactly what it
        # buys, is otherwise random and irrelevant to what this test
        # checks.
        modes = {code: FactionMode.NEUTRAL for code in real_data.factions()}
        modes['NAA'] = FactionMode.BOT
        modes['AAC'] = FactionMode.BOT
        gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
        self.assertFalse(gs.allow_combat_moves_first_turn)
        stats = GameStats()
        engine = GameEngine(gs, stats=stats)
        first_faction = gs.active_faction
        bots = {
            'NAA': RandomBot(engine, 'NAA', rng=__import__('random').Random(1)),
            'AAC': RandomBot(engine, 'AAC', rng=__import__('random').Random(2)),
        }

        play_to_completion(engine, bots, max_turns=1)

        # A weak "treasury != starting value" check would be flaky here
        # by coincidence alone -- if the bot happens to spend exactly
        # what that turn's income collects, the net change is legitimately
        # zero even though both purchase and income both did fire. These
        # two are the real, unambiguous proof the bug is fixed.
        self.assertTrue(any(k[0] == first_faction for k in stats.deployed), 'purchased units must actually deploy, not just sit pending')
        self.assertIn(first_faction, stats.cumulative_mpc, 'income must actually be collected on the first turn')

    def test_same_full_seed_reproduces_an_identical_game(self):
        # A real bug this session: GameEngine.resolve_combat used to
        # fall back to a brand-new, UNSEEDED random.Random() on every
        # single call when no rng was passed -- so even a fully-seeded
        # setup (build_game_state's rng + every bot's own seeded rng)
        # still produced a DIFFERENT game every run, since combat dice
        # (the single biggest driver of who wins/loses/gets eliminated)
        # were never actually part of the seed. Fixed by giving
        # GameEngine a persistent combat_rng, used by resolve_combat
        # whenever a call doesn't pass its own override. This test plays
        # the SAME fully-seeded setup twice and requires byte-identical
        # results -- turn count, every capture, and final unit stats.
        def play_once():
            modes = {code: FactionMode.BOT for code in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
            gs = build_game_state(
                'starting_setup_200ipc', modes, max_alliance_size=3,
                alliance_strategies={c: 'random' for c in modes},
                alliance_behaviors={c: 'random' for c in modes},
                rng=__import__('random').Random(99),
            )
            stats = GameStats()
            engine = GameEngine(gs, stats=stats, combat_rng=__import__('random').Random(1234))
            bots = {c: RandomBot(engine, c, rng=__import__('random').Random(hash(c) % 9973)) for c in modes}
            turns = play_to_completion(engine, bots, max_turns=60)
            return turns, stats.report(gs)

        turns1, report1 = play_once()
        turns2, report2 = play_once()
        self.assertEqual(turns1, turns2)
        self.assertEqual(report1, report2)

    def test_survives_the_active_faction_eliminating_itself_mid_turn(self):
        # A real, pre-existing bug found this session (reproduces on
        # prior commits too, unrelated to any of the above): a faction
        # CAN become eliminated during its own turn, not just someone
        # else's -- the clearest case is an ally-defended territory.
        # AAC owns territory 1 (its only Strategic Center), contested by
        # AAC vs a non-allied X; AAC's and X's own units there have
        # already died in earlier rounds, but AAC's ally Y's co-stationed
        # units survive. On AAC's own Capture Territory phase,
        # _determine_capture_winner hands the territory to Y instead (Y
        # is the only land presence left, per the documented "ally
        # claims it, not faction" rule) -- dropping AAC itself to 0
        # Strategic Centers, eliminating it mid-turn. Before the fix,
        # deploy_and_collect_income('AAC') right after would then raise
        # ('AAC is not an active faction'), crashing play_to_completion
        # outright instead of just ending AAC's turn early.
        data = FakeData(territories={1: {'type': 'land', 'value': 5, 'strategic_center': True}}, adjacency={})
        ally_unit = make_unit('Infantry', 'Y')
        gs = make_state(
            data, {1: 'AAC'},
            {'AAC': FactionMode.BOT, 'Y': FactionMode.BOT, 'X': FactionMode.BOT},
            phase=Phase.CAPTURE, contested={1: {'AAC', 'X'}}, units_by_territory={1: [ally_unit]},
        )
        gs.factions['AAC'].alliance = 'pact'
        gs.factions['Y'].alliance = 'pact'
        gs.active_faction = 'AAC'
        engine = GameEngine(gs, data)
        bots = {'AAC': RandomBot(engine, 'AAC')}

        turns = play_to_completion(engine, bots, max_turns=1)  # must not raise

        self.assertEqual(turns, 1)
        self.assertEqual(gs.territories[1].owner, 'Y', "AAC's ally claims the territory, per the documented rule")
        self.assertTrue(gs.factions['AAC'].eliminated, 'AAC drops to 0 Strategic Centers and is eliminated')

    def test_true_territory_loss_can_eliminate_the_active_faction_with_no_ally(self):
        # The simpler, more direct path the user pointed out afterward:
        # no ally needed at all. AAC owns territory 1 (its only Strategic
        # Center), already contested by a non-allied X from an earlier
        # turn. On AAC's own Combat Resolution phase (attacker_always_
        # solo labels AAC "attacker" here even though it's really just
        # defending its own ground), AAC's own Infantry dies while X's
        # survives -- combat.true_territory_loss (GameEngine.
        # _true_territory_loss_winner, see TestTrueTerritoryLoss in
        # test_engine.py for the mechanism itself) transfers ownership
        # straight to X and drops AAC to 0 Strategic Centers, eliminating
        # it mid-turn -- same crash risk as the ally case above, fixed
        # the same way.
        data = FakeData(territories={1: {'type': 'land', 'value': 5, 'strategic_center': True}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC')
        attacker_unit = make_unit('Infantry', 'X')
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': FactionMode.BOT, 'X': FactionMode.BOT},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'AAC', 'X'}},
            units_by_territory={1: [defender_unit, attacker_unit]},
        )
        gs.active_faction = 'AAC'
        # AAC (attacker role) rolls 1 -- misses regardless; X (defender
        # role) rolls 5 -- a clean hit, kills AAC's hp-2 Infantry.
        engine = GameEngine(gs, data, combat_rng=ScriptedRNG([1, 5]))
        bots = {'AAC': RandomBot(engine, 'AAC')}

        turns = play_to_completion(engine, bots, max_turns=1)  # must not raise

        self.assertEqual(turns, 1)
        self.assertEqual(gs.territories[1].owner, 'X')
        self.assertTrue(gs.factions['AAC'].eliminated, 'AAC drops to 0 Strategic Centers and is eliminated')


if __name__ == '__main__':
    unittest.main()
