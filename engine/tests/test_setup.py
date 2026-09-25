import random
import unittest

from engine import data
from engine.setup import build_game_state
from engine.state import FactionMode


def _all_units(gs):
    return [u for t in gs.territories.values() for u in t.units]


def _setup_units(kind, faction):
    setup, _ = data.initial_setup(kind)
    return [u for loc in setup['locations'] for u in loc['units'] if u['faction_id'] == faction]


class TestSetup(unittest.TestCase):
    def test_200ipc_scenario_unit_counts_match(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        for fac in data.factions():
            expected = len(_setup_units('standard', fac))
            actual = sum(1 for u in _all_units(gs) if u.owner == fac)
            self.assertEqual(actual, expected, f'{fac} unit count mismatch')

    def test_naval_units_deploy_to_sea_zones(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        unit_defs = data.units()
        terrs = data.territories()
        for t in gs.territories.values():
            for u in t.units:
                if unit_defs[u.unit_type]['category'] == 'Sea':
                    self.assertEqual(terrs[t.territory_id]['type'], 'sea',
                                      f'{u.unit_type} ({u.owner}) landed on {t.territory_id}, not a sea zone')

    def test_every_air_unit_at_sea_starts_with_its_carrier(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        unit_defs = data.units()
        terrs = data.territories()
        escorts = 0
        for t in gs.territories.values():
            if terrs[t.territory_id]['type'] != 'sea':
                continue
            for u in t.units:
                if unit_defs[u.unit_type]['category'] == 'Air':
                    escorts += 1
                    self.assertTrue(any(c.owner == u.owner and c.unit_type == 'Aircraft Carrier' for c in t.units),
                                    f'{u.owner} {u.unit_type} in {t.territory_id} has no carrier there')
        self.assertGreater(escorts, 0)

    def test_promotions_applied(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        _, promotions = data.initial_setup('standard')
        for fac in data.factions():
            expected = sum(1 for p in promotions['units'] if p['unit_id'].startswith(fac + '-'))
            actual = sum(1 for u in _all_units(gs) if u.owner == fac and u.promoted)
            self.assertEqual(actual, expected, f'{fac} promotion count mismatch')

    def test_defensive_mode_uses_the_defensive_setup(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['AAC'] = FactionMode.DEFENSIVE
        gs = build_game_state(modes)
        expected = len(_setup_units('defensive', 'AAC'))
        actual = sum(1 for u in _all_units(gs) if u.owner == 'AAC')
        self.assertEqual(actual, expected)

    def test_neutral_mode_gets_zero_units(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['PAF'] = FactionMode.NEUTRAL
        gs = build_game_state(modes)
        actual = sum(1 for u in _all_units(gs) if u.owner == 'PAF')
        self.assertEqual(actual, 0)

    def test_active_faction_is_first_human_or_bot(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.NEUTRAL
        # randomize_play_order defaults True (game_start_settings) --
        # disable it here since this test checks a specific, deterministic
        # first-mover, not the randomization itself.
        gs = build_game_state(modes, randomize_play_order=False)
        self.assertEqual(gs.active_faction, 'UE')
        self.assertNotIn('NAA', gs.active_factions())

    def test_starting_treasury_is_31_mpc_for_every_faction(self):
        # setup.territory_ipc_per_faction (25) + strategic_centers_per_faction
        # (3) x strategic_center_value_bonus (2) = 31 -- confirmed against
        # every faction's real territories.json data, not just the formula.
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        for fac in data.factions():
            self.assertEqual(gs.factions[fac].treasury_mpc, 31, f'{fac} starting treasury mismatch')

    def test_randomize_play_order_shuffles_active_factions(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        unshuffled = build_game_state(modes, randomize_play_order=False).active_factions()
        # A fixed seed makes this deterministic -- just needs to differ
        # from the unshuffled (JSON insertion) order to prove the shuffle
        # actually ran, not any particular resulting order.
        shuffled = build_game_state(
            modes, randomize_play_order=True, rng=random.Random(1),
        ).active_factions()
        self.assertEqual(set(shuffled), set(unshuffled))
        self.assertNotEqual(shuffled, unshuffled)

    def test_randomize_play_order_false_keeps_json_insertion_order(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes, randomize_play_order=False)
        self.assertEqual(gs.active_factions(), list(data.factions()))

    def test_game_start_settings_default_to_no_combat_moves_yes_noncombat_moves_first_turn(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        gs = build_game_state(modes)
        self.assertFalse(gs.allow_combat_moves_first_turn)
        self.assertTrue(gs.allow_noncombat_moves_first_turn)

    def test_variable_alliance_settings_get_an_initial_concrete_reroll(self):
        # A 'variable' strategy/behavior gets its first concrete pick
        # right away at setup too, not just at the bot's own first
        # Purchase phase (RandomBot._maybe_reroll_variable_alliance_
        # settings) -- so there's a real decision from turn 1, not None,
        # e.g. if another bot invites it before its own first turn.
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.BOT
        gs = build_game_state(
            modes,
            alliance_strategies={'NAA': 'variable'}, alliance_behaviors={'NAA': 'variable'},
            rng=random.Random(1),
        )
        self.assertEqual(gs.factions['NAA'].alliance_strategy, 'variable', 'stays variable all game')
        self.assertEqual(gs.factions['NAA'].alliance_behavior, 'variable')
        self.assertIsNotNone(gs.factions['NAA'].current_alliance_strategy)
        self.assertIsNotNone(gs.factions['NAA'].current_alliance_behavior)
        self.assertNotEqual(gs.factions['NAA'].current_alliance_strategy, 'variable')
        self.assertNotEqual(gs.factions['NAA'].current_alliance_behavior, 'variable')

    def test_non_variable_alliance_settings_leave_current_fields_unset(self):
        modes = {c: FactionMode.HUMAN for c in data.factions()}
        modes['NAA'] = FactionMode.BOT
        gs = build_game_state(
            modes,
            alliance_strategies={'NAA': 'aggressive'}, alliance_behaviors={'NAA': 'loyal'},
            rng=random.Random(1),
        )
        self.assertIsNone(gs.factions['NAA'].current_alliance_strategy)
        self.assertIsNone(gs.factions['NAA'].current_alliance_behavior)


if __name__ == '__main__':
    unittest.main()


class TestStartingAlliances(unittest.TestCase):
    def _modes(self, **kw):
        modes = {c: FactionMode.NEUTRAL for c in ('NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC')}
        modes.update({k: v for k, v in kw.items()})
        return modes

    def test_members_start_allied_and_share_one_tag(self):
        modes = self._modes(NAA=FactionMode.HUMAN, UE=FactionMode.BOT, GPC=FactionMode.BOT, AAC=FactionMode.BOT)
        gs = build_game_state(modes, randomize_play_order=False,
                              max_alliance_size=2, starting_alliances=[['NAA', 'UE']])
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['UE'].alliance)
        self.assertIsNotNone(gs.factions['NAA'].alliance)
        self.assertIsNone(gs.factions['GPC'].alliance)

    def test_two_groups_get_different_tags(self):
        modes = self._modes(NAA=FactionMode.BOT, UE=FactionMode.BOT, GPC=FactionMode.BOT, AAC=FactionMode.BOT, PAF=FactionMode.BOT)
        gs = build_game_state(modes, randomize_play_order=False,
                              max_alliance_size=2, starting_alliances=[['NAA', 'UE'], ['GPC', 'AAC']])
        self.assertNotEqual(gs.factions['NAA'].alliance, gs.factions['GPC'].alliance)
        self.assertIsNone(gs.factions['PAF'].alliance)

    def test_new_alliances_formed_later_do_not_reuse_a_starting_tag(self):
        modes = self._modes(NAA=FactionMode.BOT, UE=FactionMode.BOT, GPC=FactionMode.BOT, AAC=FactionMode.BOT)
        gs = build_game_state(modes, randomize_play_order=False,
                              max_alliance_size=2, starting_alliances=[['NAA', 'UE']])
        self.assertEqual(gs._next_alliance_id, 2)

    def test_rejected_setups(self):
        bot = FactionMode.BOT
        cases = {
            'a group of one': (self._modes(NAA=bot, UE=bot, GPC=bot), [['NAA']], 3),
            'a neutral member': (self._modes(NAA=bot, UE=bot, GPC=bot), [['NAA', 'AAC']], 3),
            'in two groups': (self._modes(NAA=bot, UE=bot, GPC=bot, AAC=bot), [['NAA', 'UE'], ['UE', 'GPC']], 3),
            'too big for max size': (self._modes(NAA=bot, UE=bot, GPC=bot, AAC=bot), [['NAA', 'UE', 'GPC']], 2),
            'everyone allied': (self._modes(NAA=bot, UE=bot, GPC=bot), [['NAA', 'UE', 'GPC']], 3),
        }
        for name, (modes, groups, size) in cases.items():
            with self.assertRaises(ValueError, msg=name):
                build_game_state(modes, randomize_play_order=False,
                                 max_alliance_size=size, starting_alliances=groups)


class TestDefensivePowersHaveNoStrategicCenters(unittest.TestCase):
    """A Defensive power's territory is never a Strategic Center -- and stays a non-SC when
    another faction captures it."""

    def setUp(self):
        from engine import data as real_data
        from engine.economy import compute_income
        from engine.engine import GameEngine
        from engine.state import GameState, FactionMode
        modes = {f: FactionMode.BOT for f in real_data.factions()}
        modes['UER'] = FactionMode.DEFENSIVE
        self.gs = build_game_state(modes, randomize_play_order=False)
        self.data = real_data
        self.terrs = real_data.territories()
        self.compute_income = compute_income
        self.GameEngine = GameEngine
        self.GameState = GameState

    def uer_scs(self):
        return [tid for tid, t in self.terrs.items() if t['type'] == 'land' and t.get('faction') == 'UER' and t.get('strategic_center')]

    def test_its_home_territories_are_switched_off_at_setup(self):
        scs = self.uer_scs()
        self.assertTrue(scs)  # the map has some
        for tid in scs:
            self.assertTrue(self.gs.territories[tid].sc_disabled)
            self.assertFalse(self.gs.is_strategic_center(tid, self.terrs[tid]))
        # Other powers' Strategic Centers are untouched.
        other = next(tid for tid, t in self.terrs.items() if t['type'] == 'land' and t.get('faction') == 'NAA' and t.get('strategic_center'))
        self.assertTrue(self.gs.is_strategic_center(other, self.terrs[other]))

    def test_capturing_one_does_not_make_it_a_strategic_center(self):
        tid = self.uer_scs()[0]
        before = self.compute_income('NAA', self.gs, self.data)
        self.gs.territories[tid].owner = 'NAA'
        self.assertFalse(self.gs.is_strategic_center(tid, self.terrs[tid]))
        self.assertEqual(self.compute_income('NAA', self.gs, self.data), before + self.terrs[tid]['value'])  # its value, no +2

    def test_it_does_not_count_toward_the_captors_strategic_center_total(self):
        from engine.state import Phase
        tid = self.uer_scs()[0]
        engine = self.GameEngine(self.gs, self.data)
        engine.game_state.phase = Phase.CAPTURE
        naa_scs = sum(1 for t, ts in self.gs.territories.items() if ts.owner == 'NAA' and self.gs.is_strategic_center(t, self.terrs[t]))
        self.gs.territories[tid].owner = 'NAA'
        again = sum(1 for t, ts in self.gs.territories.items() if ts.owner == 'NAA' and self.gs.is_strategic_center(t, self.terrs[t]))
        self.assertEqual(naa_scs, again)

    def test_the_status_survives_a_save(self):
        tid = self.uer_scs()[0]
        again = self.GameState.from_dict(self.gs.to_dict())
        self.assertTrue(again.territories[tid].sc_disabled)
        self.assertFalse(again.is_strategic_center(tid, self.terrs[tid]))

    def test_a_purchase_there_pays_the_ordinary_price_and_capacity(self):
        # A captured former-Defensive SC is an ordinary territory for buying too.
        tid = self.uer_scs()[0]
        self.gs.territories[tid].owner = 'NAA'
        engine = self.GameEngine(self.gs, self.data)
        unit = self.data.units()['Infantry']
        self.assertEqual(engine._unit_cost('Infantry', tid), unit['cost'])
        self.assertEqual(engine._deploy_cap(tid), self.terrs[tid]['value'])
