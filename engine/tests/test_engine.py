import random
import unittest

from engine import data as real_data
from engine.combat import BattleResult
from engine.engine import GameEngine, PurchaseOrder, CombatMoveOrder, NonCombatMoveOrder
from engine.turn_log import TurnLog
from engine.state import GameState, TerritoryState, FactionState, UnitInstance, FactionMode, Phase
from engine.stats import GameStats

UNIT_DEFS = {
    'Infantry': {'category': 'Land', 'cost': 3, 'sc_cost': 2, 'hp': 2, 'purchasable': True,
                 'attack_die': 'D6', 'defense': 5, 'damage': 2, 'combat_move': 1, 'non_combat_move': 2},
    'Mechanized Infantry': {'category': 'Land', 'cost': 6, 'sc_cost': 4, 'hp': 3, 'purchasable': True,
                             'attack_die': 'D6', 'defense': 6, 'damage': 2, 'combat_move': 2, 'non_combat_move': 2,
                             'special_abilities': ['Amphibious: becomes a transport in sea spaces']},
    'Armor': {'category': 'Land', 'cost': 8, 'sc_cost': 6, 'hp': 4, 'purchasable': True,
              'attack_die': 'D8', 'defense': 7, 'damage': 3, 'combat_move': 1, 'non_combat_move': 2},
    'Cruiser': {'category': 'Sea', 'cost': 11, 'sc_cost': 8, 'hp': 5, 'purchasable': True,
                'attack_die': 'D10', 'defense': 7, 'damage': 3, 'combat_move': 2, 'non_combat_move': 2},
    'Fighter': {'category': 'Air', 'cost': 10, 'sc_cost': 7, 'hp': 2, 'purchasable': True,
                'attack_die': 'D8', 'defense': 8, 'damage': 3, 'combat_move': 2, 'non_combat_move': 3},
    'Aircraft Carrier': {'category': 'Sea', 'cost': 14, 'sc_cost': 10, 'hp': 6, 'purchasable': True,
                          'attack_die': None, 'defense': 6, 'damage': None, 'combat_move': 2, 'non_combat_move': 2},
    'Transport': {'category': 'Sea', 'cost': None, 'sc_cost': None, 'hp': 1, 'purchasable': False,
                  'attack_die': None, 'defense': 6, 'damage': None, 'combat_move': 2, 'non_combat_move': 2},
}


class FakeData:
    """A hand-built, fully controlled territory graph -- the real
    149-territory map isn't practical for hand-verifying an exact
    multi-territory spillover allocation."""
    def __init__(self, territories, adjacency, unit_defs=None):
        self._territories = territories  # {id: {'type', 'value'?, 'strategic_center'?}}
        self._adjacency = adjacency
        self._units = unit_defs or UNIT_DEFS

    def units(self):
        return self._units

    def territories(self):
        return self._territories

    def rules(self):
        # combat.resolve_battle needs the real ruleset (resolution
        # order, target-selection weighting, promotion thresholds,
        # air-superiority trigger) -- only territories/adjacency/units
        # are faked for movement/purchase test control, not the combat
        # rules themselves.
        return real_data.rules()

    def adjacency(self):
        return self._adjacency


def make_state(data, territory_owners, faction_modes, treasury=None, contested=None,
                units_by_territory=None, pending_by_territory=None, phase=Phase.PURCHASE, global_turn=0):
    gs = GameState(phase=phase, global_turn=global_turn)
    for code, mode in faction_modes.items():
        gs.factions[code] = FactionState(code=code, mode=mode, treasury_mpc=(treasury or {}).get(code, 1000))
    for tid in data.territories():
        gs.territories[tid] = TerritoryState(
            territory_id=tid, owner=territory_owners.get(tid),
            contested_by=(contested or {}).get(tid),
            units=(units_by_territory or {}).get(tid, []),
            pending_deployment=(pending_by_territory or {}).get(tid, []),
        )
    return gs


_next_uid = [1000]


def make_unit(unit_type, owner, purchased_at=None, hp=None):
    _next_uid[0] += 1
    hp = UNIT_DEFS[unit_type]['hp'] if hp is None else hp
    return UnitInstance(unit_id=_next_uid[0], unit_type=unit_type, owner=owner, current_hp=hp, purchased_at=purchased_at)


class TestLegalPurchaseTargets(unittest.TestCase):
    """Moved here from engine.bots.random_bot.RandomBot's own former
    private copy this session -- any caller (a UI wanting to show legal
    choices, not just a bot) can use it now."""

    def test_owned_land_split_by_strategic_center(self):
        data = FakeData(
            territories={1: {'type': 'land', 'strategic_center': True}, 2: {'type': 'land'}}, adjacency={},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        sc_targets, other_targets = engine.legal_purchase_targets('NAA')
        self.assertEqual(set(sc_targets), {1})
        self.assertEqual(set(other_targets), {2})

    def test_sea_zone_adjacent_to_owned_sc_counts_as_an_sc_target(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}, 2: {'type': 'sea'}},
            adjacency={1: [2], 2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        sc_targets, other_targets = engine.legal_purchase_targets('NAA')
        self.assertEqual(set(sc_targets), {1, 2})
        self.assertEqual(other_targets, [])

    def test_unowned_and_unreachable_territories_are_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        sc_targets, other_targets = engine.legal_purchase_targets('NAA')
        self.assertEqual(set(other_targets), {1})
        self.assertNotIn(2, other_targets)

    def test_a_sea_zone_whose_only_funding_source_is_contested_is_not_a_legal_target(self):
        # purchase.contested_land_deploy_restriction: a contested territory's port can't fund a
        # deploy out into adjacent water any more than it can host anything but Infantry itself.
        data = FakeData(territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'sea'}}, adjacency={1: [2], 2: [1]})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        sc_targets, other_targets = engine.legal_purchase_targets('NAA')
        self.assertNotIn(2, set(sc_targets) | set(other_targets))


class TestLegalCombatMoveOptions(unittest.TestCase):
    """GameEngine.legal_combat_move_options -- the "known at turn start,
    client picks from these" query this session's server work (Combat
    Move as a real human decision point) is built on."""

    def test_a_unit_with_a_legal_attack_is_included(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertIn(mover.unit_id, options)
        entry = options[mover.unit_id]
        self.assertEqual(entry['unit_type'], 'Infantry')
        self.assertEqual(entry['territory_id'], 1)
        self.assertEqual(entry['destinations'], {2: [1, 2]})

    def test_a_unit_that_already_moved_is_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertNotIn(mover.unit_id, options)

    def test_a_unit_with_no_legal_destination_is_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})  # no neighbors at all
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertEqual(options, {})

    def test_another_factions_units_are_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'AAC')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={2: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertEqual(options, {})

    def test_air_unit_destinations_are_two_entry_paths(self):
        # Air can't capture, so an air combat move needs an actual
        # non-ally-occupied (or already contested) destination -- an
        # empty foreign territory is never a legal air attack target.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        flyer = make_unit('Fighter', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [flyer], 2: [defender]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertEqual(options[flyer.unit_id]['destinations'], {2: [1, 2]})

    def test_air_unit_continuations_are_always_empty(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        flyer = make_unit('Fighter', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [flyer], 2: [defender]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertEqual(options[flyer.unit_id]['continuations'], {})

    def test_mech_inf_continuations_surface_a_second_hop_after_a_pass_through_capture(self):
        # 1 (origin) -- 2 (empty enemy) -- 3 (empty enemy, beyond 2): the
        # server-facing shape of movement.legal_combat_move_continuations,
        # embedded right alongside 'destinations' for the client to use.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
                         adjacency={1: [2], 2: [1, 3], 3: [2]})
        mover = make_unit('Mechanized Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_combat_move_options('NAA')
        self.assertEqual(options[mover.unit_id]['continuations'], {2: {3: [1, 2, 3]}})


class TestSeaUnitsCannotBeOrderedOntoLand(unittest.TestCase):
    """The engine-level face of movement.sea_units_stay_at_sea: neither the
    legal-option queries nor an actual submitted order may put a ship on
    land."""

    def _setup(self):
        # Aircraft Carrier, not Cruiser: a Cruiser specifically CAN target enemy-
        # occupied land now (combat.cruiser_bombardment) -- see
        # TestCruiserBombardment for that exception; this class is testing the
        # general sea_units_stay_at_sea rule every OTHER sea unit (and a
        # Cruiser's own non-bombardment moves) still follows.
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        ship = make_unit('Aircraft Carrier', 'NAA')
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [ship], 2: [make_unit('Infantry', 'AAC')]},
        )
        return GameEngine(gs, data), ship

    def test_legal_combat_move_options_never_offer_land_to_a_ship(self):
        engine, ship = self._setup()
        self.assertNotIn(ship.unit_id, engine.legal_combat_move_options('NAA'))

    def test_a_submitted_combat_move_onto_land_is_rejected(self):
        engine, ship = self._setup()
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(ship.unit_id, [1, 2])])


class TestCruiserBombardmentEndToEnd(unittest.TestCase):
    """rules.json's combat.cruiser_bombardment, the full turn: Combat Move
    (declaring it) through Combat Resolution (it actually firing)."""

    def _setup(self, stats=None, turn_log=None):
        # 1 (sea, NAA's Cruiser) -- 2 (land, AAC, one defending Infantry).
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        cruiser = make_unit('Cruiser', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [cruiser], 2: [defender]},
        )
        engine = GameEngine(gs, data, stats=stats, turn_log=turn_log)
        return engine, gs, cruiser, defender

    def test_declaring_it_leaves_the_cruiser_in_its_sea_zone_uncontested(self):
        engine, gs, cruiser, defender = self._setup()
        engine.submit_combat_moves('NAA', [CombatMoveOrder(cruiser.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(cruiser, gs.territories[1].units, 'the Cruiser never actually enters the land territory')
        self.assertEqual(cruiser.bombard_target, 2)
        self.assertTrue(cruiser.has_moved_combat)
        self.assertIsNone(gs.territories[2].contested_by, 'a pure bombardment declaration never contests the territory')

    def test_it_fires_at_the_start_of_combat_resolution_and_can_kill(self):
        stats = GameStats()
        log = TurnLog()
        engine, gs, cruiser, defender = self._setup(stats=stats, turn_log=log)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(cruiser.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.COMBAT_RESOLUTION)
        # Infantry: defense 5 (6 defending, Dig In); Cruiser: D10, damage 3.
        engine.resolve_combat('NAA', rng=ScriptedRNG([6]))
        self.assertNotIn(defender, gs.territories[2].units, 'the bombardment killed it before any real battle')
        self.assertIsNone(cruiser.bombard_target, 'consumed')
        self.assertEqual(cruiser.xp, 0, 'no XP for a bombardment')
        self.assertEqual(stats.kills[('NAA', 'Cruiser')], 1)
        self.assertEqual(stats.deaths[('AAC', 'Infantry')], 1)
        bombardments = [e for e in log.events if e['kind'] == 'bombardment']
        self.assertEqual(len(bombardments), 1)
        self.assertEqual(bombardments[0]['eliminated'], True)
        self.assertEqual(bombardments[0]['territory_id'], 2)

    def test_it_does_not_prevent_the_real_battle_from_still_happening_after(self):
        # A land attacker joins the same turn -- the bombardment fires first (and
        # can soften the defender), then the actual battle still resolves as its
        # own, separate thing (combat.cruiser_bombardment's separate_tally).
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        cruiser = make_unit('Cruiser', 'NAA')
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {2: 'AAC', 3: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [cruiser], 2: [defender], 3: [attacker]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(cruiser.unit_id, [1, 2]), CombatMoveOrder(attacker.unit_id, [3, 2])])
        engine.confirm_combat_moves('NAA')
        engine.advance_phase()
        # Bombardment roll (6, kills the sole defender) leaves nothing for the
        # land battle to actually fight -- resolve_combat still runs it, a
        # trivial, immediate defender_eliminated.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([6]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, 'defender_eliminated')
        self.assertIn(attacker, gs.territories[2].units)


class TestLegalNoncombatMoveOptions(unittest.TestCase):
    """GameEngine.legal_noncombat_move_options -- the Non-Combat Move
    counterpart to legal_combat_move_options, queried after Combat
    Resolution (and, for a human, after process_return_to_base) rather
    than at turn start."""

    def test_a_unit_with_a_legal_destination_is_included(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertIn(mover.unit_id, options)
        entry = options[mover.unit_id]
        self.assertEqual(entry['unit_type'], 'Infantry')
        self.assertEqual(entry['territory_id'], 1)
        self.assertEqual(entry['destinations'], [2], 'a plain list of ids, not {destination: path} -- no route to report')

    def test_a_unit_that_already_noncombat_moved_is_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_noncombat = True
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertNotIn(mover.unit_id, options)

    def test_a_land_unit_that_already_combat_moved_is_excluded(self):
        # combat_or_noncombat_not_both: a non-air unit that combat-moved
        # this turn may not also non-combat-move.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertNotIn(mover.unit_id, options)

    def test_an_air_unit_that_already_combat_moved_is_still_included(self):
        # Air is the sole exception to combat_or_noncombat_not_both --
        # still eligible for its own non-combat move even after already
        # combat-moving this same turn.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.has_moved_combat = True
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [flyer]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertIn(flyer.unit_id, options)
        self.assertEqual(options[flyer.unit_id]['destinations'], [2])

    def test_a_unit_with_no_legal_destination_is_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})  # no neighbors at all
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertEqual(options, {})

    def test_another_factions_units_are_excluded(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'AAC')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={2: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertEqual(options, {})

    def test_clean_foreign_territory_is_not_a_legal_destination(self):
        # noncombat_move_destination: never a clean (uncontested),
        # non-allied foreign territory -- only combat move can attack.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [mover]})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertEqual(options, {})

    def test_contested_foreign_territory_becomes_a_legal_destination(self):
        # Same territory as above, but now contested (e.g. by this same
        # turn's own earlier Combat Move) -- noncombat_move_destination
        # allows reinforcing ANY already-contested territory regardless
        # of who owns/contests it. Demonstrates why this query has to be
        # computed fresh once Non-Combat Move is reached, not reused from
        # a turn-start snapshot: this same unit had zero legal moves
        # before the territory became contested.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2], 2: [1]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
                         units_by_territory={1: [mover]}, contested={2: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        options = engine.legal_noncombat_move_options('NAA')
        self.assertEqual(options[mover.unit_id]['destinations'], [2])


class TestLandPurchase(unittest.TestCase):
    def test_buy_within_capacity_and_confirm(self):
        # territory 1: land, value 2, owned by NAA -- cap 2.
        data = FakeData(territories={1: {'type': 'land', 'value': 2}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        self.assertEqual(cost, 6)  # 2 x cost 3
        engine.confirm_purchases('NAA')
        self.assertEqual(len(gs.territories[1].pending_deployment), 2)
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 1000 - 6)

    def test_strategic_center_uses_sc_cost(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 2, 'strategic_center': True}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        self.assertEqual(cost, 4)  # 2 x sc_cost 2

    def test_exceeding_single_territory_capacity_is_rejected(self):
        # cap = value 2 + SC bonus 0 = 2; ordering 3 units should fail.
        data = FakeData(territories={1: {'type': 'land', 'value': 2}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 3, 1)])

    def test_sea_category_unit_cannot_deploy_on_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 1, 1)])

    def test_unowned_land_target_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])

    def test_insufficient_treasury_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, treasury={'NAA': 2})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # costs 3, only 2 available


class TestContestedLandDeployRestriction(unittest.TestCase):
    def test_infantry_allowed_into_contested_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # should not raise

    def test_non_infantry_rejected_from_contested_land(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Armor', 1, 1)])

    def test_a_contested_land_territory_cannot_fund_a_deploy_into_an_adjacent_sea_zone(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={1: [2], 2: [1]})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 1, 2)])

    def test_a_contested_source_is_skipped_in_favour_of_an_uncontested_neighbour(self):
        # 3 (sea) touches 1 (contested, value 5 -- would otherwise be plenty) and 2 (uncontested, value 2).
        # The purchase must draw entirely from 2, so a quantity exceeding 2's cap fails outright even
        # though the (excluded) contested territory 1 alone could easily have covered it.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 2}, 3: {'type': 'sea'}},
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, contested={1: {'NAA', 'AAC'}})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 2, 3)])
        self.assertEqual(cost, 22)  # 2 x cost 11, both drawn from territory 2 (cap 2) -- none from contested 1
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 3, 3)])  # exceeds territory 2's cap alone


class TestSeaDeployAllocation(unittest.TestCase):
    def test_single_adjacent_territory_supplies_cost_and_cap(self):
        # 1 (land, value 3, SC) -- 2 (sea)
        data = FakeData(
            territories={1: {'type': 'land', 'value': 3, 'strategic_center': True}, 2: {'type': 'sea'}},
            adjacency={2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 2, 2)])
        self.assertEqual(cost, 16)  # 2 x sc_cost 8 (SC's cap is 3+2=5, plenty of room)

    def test_no_adjacent_owned_territory_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Cruiser', 1, 2)])

    def test_sc_drawn_first_regardless_of_raw_value(self):
        # 3 (sea) adjacent to 1 (land, value 5, no SC -- cap 5) and
        # 2 (land, value 1, SC -- cap 3). SC goes first even though its
        # raw value/cap is smaller.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        # Buy exactly 3 (the SC's full capacity) of the one land unit that can
        # be deployed at sea -- if the SC goes first, all 3 should be sc_cost
        # (3 x 4 = 12); if the larger territory went first instead, they'd
        # be full cost (3 x 6 = 18).
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Mechanized Infantry', 3, 3)])
        self.assertEqual(cost, 12)

    def test_spillover_across_multiple_territories_within_one_order(self):
        # Same setup as above: SC (territory 2, cap 3) then territory 1
        # (cap 5). Buying 5 Mechanized Infantry should draw 3 from the SC
        # (sc_cost 4 each = 12) and the remaining 2 from territory 1 (cost 6
        # each = 12), spilling over automatically within a single order.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        cost = engine.submit_purchases('NAA', [PurchaseOrder('Mechanized Infantry', 5, 3)])
        self.assertEqual(cost, 12 + 12)

    def test_spillover_exhausted_across_all_sources_is_rejected(self):
        # SC cap 3 + territory 1 cap 5 = 8 total; asking for 9 must fail.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5},
                2: {'type': 'land', 'value': 1, 'strategic_center': True},
                3: {'type': 'sea'},
            },
            adjacency={3: [1, 2]},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Mechanized Infantry', 9, 3)])

    def test_two_orders_in_the_same_list_compete_for_the_same_capacity(self):
        # A single land territory (cap 5) backing a sea zone. Two
        # separate orders in the SAME submit_purchases call, totaling 6
        # units, should fail even though each order alone would fit --
        # capacity tracking is shared across the whole submitted list.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}},
            adjacency={2: [1]},
        )
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Mechanized Infantry', 3, 2), PurchaseOrder('Mechanized Infantry', 3, 2)])

    def test_mech_inf_purchased_at_sea_is_still_just_a_normal_unit_instance(self):
        # No special "Transport" bookkeeping at purchase time -- it's
        # purely a placement detail (a Mechanized Infantry sitting in a sea
        # zone is riding a transport); confirm_purchases just places the
        # ordered unit type.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Mechanized Infantry', 1, 2)])
        engine.confirm_purchases('NAA')
        placed = gs.territories[2].pending_deployment
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].unit_type, 'Mechanized Infantry')

    def test_infantry_and_armor_cannot_be_purchased_into_the_water(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        for unit in ('Infantry', 'Armor'):
            with self.assertRaises(ValueError):
                engine.submit_purchases('NAA', [PurchaseOrder(unit, 1, 2)])


class TestRollbackAndConfirmation(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 2, 1)])
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])  # "undo" down to 1
        engine.confirm_purchases('NAA')
        self.assertEqual(len(gs.territories[1].pending_deployment), 1)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])
        engine.confirm_purchases('NAA')
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])
        with self.assertRaises(ValueError):
            engine.confirm_purchases('NAA')

    def test_confirm_with_no_prior_submission_is_a_harmless_no_op(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        engine = GameEngine(gs, data)
        engine.confirm_purchases('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 1000)

    def test_defensive_faction_cannot_submit_purchases(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.DEFENSIVE})
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN})
        gs.phase = Phase.COMBAT_MOVE
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, 1)])


class TestDeployPending(unittest.TestCase):
    def test_land_deploy_to_still_owned_territory(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1)
        self.assertEqual(len(gs.territories[1].pending_deployment), 0)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.deploy_and_collect_income('NAA')


class TestContestedPurchaseLostFallback(unittest.TestCase):
    def test_falls_back_to_adjacent_controlled_territory(self):
        # territory 1 (pending Infantry, now owned by AAC -- lost this
        # turn) -- territory 2 (still owned by NAA) -- territory 3 (sea).
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 1)
        self.assertEqual(len(gs.territories[1].units), 0)

    def test_is_lost_when_only_water_is_adjacent(self):
        # Infantry cannot enter the water, so a sea zone is no fallback.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[3].units), 0)
        self.assertEqual(len(gs.territories[1].units), 0)

    def test_lost_outright_when_no_adjacent_controlled_or_sea(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}},
            adjacency={1: [2]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 0)
        self.assertEqual(len(gs.territories[1].units), 0)

    def test_a_lost_purchase_prefers_land_over_water_and_is_recorded_where_it_lands(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 3}, 3: {'type': 'sea'}},
            adjacency={1: [2, 3]},
        )
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        log = TurnLog()
        engine = GameEngine(gs, data, turn_log=log)
        engine.deploy_and_collect_income('NAA')
        deploys = [e for e in log.events if e['kind'] == 'unit_deployed']
        self.assertEqual([(e['territory_id'], e['qty']) for e in deploys], [(2, 1)])
        self.assertEqual(len(gs.territories[3].units), 0)

    def test_still_owned_territory_is_unaffected(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            contested={1: {'NAA', 'AAC'}},  # still contested but NOT lost
            pending_by_territory={1: [make_unit('Infantry', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1, 'still-owned (even if contested) territory needs no fallback')


class TestLostContestedPurchaseOverAWholeTurn(unittest.TestCase):
    """The whole turn on the real map: Infantry bought into a contested territory that is then
    lost in the same turn's combat still get deployed on adjacent land; with none left they are lost
    (Infantry cannot enter the water)."""

    def play(self, hand_over_to_gpc=()):
        from engine.setup import build_game_state
        modes = {f: FactionMode.BOT for f in real_data.factions()}
        gs = build_game_state('starting_setup_125ipc', modes, randomize_play_order=False)
        engine = GameEngine(gs, real_data, combat_rng=random.Random(1))
        T = 10  # Western Canada, NAA's, with one NAA Infantry in it
        for i in range(4):  # GPC's promoted Armor holds it against that lone defender
            gs.territories[T].units.append(UnitInstance(unit_id=9000 + i, unit_type='Armor', owner='GPC', current_hp=4, promotions=3))
        gs.territories[T].contested_by = {'GPC', 'NAA'}
        for n in hand_over_to_gpc:
            gs.territories[n].owner = 'GPC'
        original = {u.unit_id for t in gs.territories.values() for u in t.units}  # the starting Infantry are not what is counted
        count = lambda: {tid: sum(1 for u in t.units if u.owner == 'NAA' and u.unit_type == 'Infantry' and u.unit_id not in original)
                         for tid, t in gs.territories.items()}
        before = count()
        engine.submit_purchases('NAA', [PurchaseOrder('Infantry', 1, T)])
        engine.confirm_purchases('NAA')
        while gs.active_faction == 'NAA' and gs.phase != Phase.DIPLOMACY:
            ph = gs.phase
            if ph == Phase.COMBAT_MOVE:
                engine.confirm_combat_moves('NAA')
            elif ph == Phase.COMBAT_RESOLUTION:
                engine.resolve_combat('NAA')
            elif ph == Phase.NONCOMBAT_MOVE:
                engine.process_return_to_base('NAA')
                engine.confirm_noncombat_moves('NAA')
            elif ph == Phase.CAPTURE:
                engine.process_capture_territory('NAA')
                self.assertEqual(gs.territories[T].owner, 'GPC', 'the battle was lost, and with it the territory')
            elif ph == Phase.DEPLOY_INCOME:
                engine.deploy_and_collect_income('NAA')
            engine.advance_phase()
        after = count()
        return {tid: after[tid] - before[tid] for tid in after if after[tid] != before[tid]}

    def test_the_infantry_fall_back_to_an_adjacent_territory_still_held(self):
        placed = self.play()
        self.assertEqual(list(placed.values()), [1])
        tid = next(iter(placed))
        self.assertEqual(real_data.territories()[tid]['type'], 'land')
        self.assertIn(tid, real_data.adjacency()[10])

    def test_with_no_adjacent_land_left_they_are_lost(self):
        # Every one of territory 10's (Western Canada's) own land neighbors: Eastern Canada (20),
        # Western United States (54), Rocky Mountain States (67), Alaska (6).
        placed = self.play(hand_over_to_gpc=(20, 54, 67, 6))
        self.assertEqual(placed, {})


class TestCarrierlessAirDeployFallback(unittest.TestCase):
    def test_redirects_to_purchasing_land_when_no_carrier_anywhere(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1)
        self.assertEqual(len(gs.territories[2].units), 0)

    def test_stays_at_sea_when_own_carrier_already_present(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Aircraft Carrier', 'NAA')]},
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 2)  # carrier + fighter

    def test_stays_at_sea_when_own_carrier_arrives_in_the_same_batch(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1), make_unit('Aircraft Carrier', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[2].units), 2)

    def test_allied_carrier_does_not_count(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Aircraft Carrier', 'UE')]},
            pending_by_territory={2: [make_unit('Fighter', 'NAA', purchased_at=1)]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(len(gs.territories[1].units), 1, "an ally's carrier shouldn't satisfy the carrierless fallback")


class TestHostileSeaDeployCreatesContested(unittest.TestCase):
    def test_deploying_into_enemy_occupied_zone_creates_contested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'AAC')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_deploying_into_enemy_occupied_zone_queues_the_ambush_bonus(self):
        # combat.first_round_bonuses' "sea deploy into enemy-occupied
        # zone" case -- the prior occupant (AAC) is queued for the bonus
        # on its own next attack here.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'AAC')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.territories[2].ambush_bonus_for, {'AAC'})

    def test_ambush_bonus_queues_every_prior_non_ally_at_once(self):
        # Two distinct non-allied factions (AAC, UER) already share the
        # zone -- confirmed this session: both get queued independently,
        # not just one.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'},
            {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UER': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'AAC'), make_unit('Cruiser', 'UER')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.territories[2].ambush_bonus_for, {'AAC', 'UER'})

    def test_deploying_into_allied_occupied_zone_stays_uncontested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            units_by_territory={2: [make_unit('Cruiser', 'UE')]},
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertIsNone(gs.territories[2].contested_by)
        self.assertEqual(gs.territories[2].ambush_bonus_for, set(), 'an allied deploy never queues the ambush bonus')

    def test_deploying_into_empty_zone_stays_uncontested(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME,
            pending_by_territory={2: [make_unit('Cruiser', 'NAA', purchased_at=1)]},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertIsNone(gs.territories[2].contested_by)


class TestIncomeCollection(unittest.TestCase):
    def test_income_added_to_treasury(self):
        data = FakeData(
            territories={1: {'type': 'land', 'value': 3}, 2: {'type': 'land', 'value': 2, 'strategic_center': True}},
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, treasury={'NAA': 10}, phase=Phase.DEPLOY_INCOME)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 10 + 3 + (2 + 2))

    def test_contested_territory_contributes_no_income(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, treasury={'NAA': 10}, phase=Phase.DEPLOY_INCOME,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 10)


class TestGlobalRecoverySweep(unittest.TestCase):
    def test_unit_heals_after_a_full_round_has_elapsed(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=2,
        )
        # 2 active factions -- damaged on global_turn 0, so a full round
        # (2 turns) has elapsed by global_turn 2.
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(damaged.current_hp, UNIT_DEFS['Armor']['hp'])

    def test_unit_does_not_heal_before_a_full_round_has_elapsed(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=1,
        )
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(damaged.current_hp, 1, 'only 1 turn elapsed, not a full round (2 active factions)')

    def test_recovery_sweeps_the_whole_board_not_just_the_active_faction(self):
        # The unit healed above belongs to AAC, not the acting faction
        # NAA -- confirming the sweep really is global.
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 5}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DEPLOY_INCOME, global_turn=2,
        )
        damaged = make_unit('Armor', 'AAC', hp=1)
        damaged.last_combat_global_turn = 0
        gs.territories[2].units.append(damaged)
        engine = GameEngine(gs, data)
        engine.deploy_and_collect_income('NAA')
        self.assertGreater(damaged.current_hp, 1)


class TestCombatMoveExecution(unittest.TestCase):
    def test_single_hop_attack_marks_contested_and_relocates_unit(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [attacker], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(attacker.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertNotIn(attacker, gs.territories[1].units)
        self.assertIn(attacker, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})
        self.assertTrue(attacker.has_moved_combat)
        self.assertEqual(gs.territories[2].owner, 'AAC', 'still contested -- not captured until the battle resolves')

    def test_single_hop_entry_into_empty_territory_marks_it_contested_not_owned(self):
        # Confirmed this session: even an entirely undefended entry
        # never captures outright -- it's marked contested, and actual
        # ownership is resolved later, in Capture Territory.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Armor', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'AAC', 'not captured immediately -- Capture Territory resolves it later')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_mech_inf_blitz_marks_every_entered_territory_contested(self):
        # Confirmed this session: "even if it's just a Mech Inf running
        # through" -- every territory entered or passed through is
        # marked contested, not just the final, defended stop.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        mover = make_unit('Mechanized Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover], 3: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2, 3])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(gs.territories[2].owner, 'AAC', 'passed through, not captured immediately')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'}, 'marked contested even though the unit kept moving')
        self.assertIn(mover, gs.territories[3].units)
        self.assertEqual(gs.territories[3].contested_by, {'NAA', 'AAC'})

    def test_air_combat_move_never_captures_only_marks_contested(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},  # territory 2 is EMPTY -- air alone can't attack nothing, so...
        )
        engine = GameEngine(gs, data)
        # ...legal_air_move_destinations correctly rejects this (no attack target).
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])

    def test_air_combat_move_attacks_occupied_territory(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})
        self.assertEqual(gs.territories[2].owner, 'AAC', "air alone can't capture")

    def test_unit_not_found_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(99999, [1, 2])])

    def test_wrong_owner_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        other_faction_unit = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC', 2: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [other_faction_unit]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(other_faction_unit.unit_id, [1, 2])])

    def test_path_origin_mismatch_is_rejected(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 3: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        # mover is actually at 1, not 2
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [2, 3])])

    def test_cannot_move_the_same_unit_twice_in_one_submission(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [
                CombatMoveOrder(mover.unit_id, [1, 2]),
                CombatMoveOrder(mover.unit_id, [1, 3]),
            ])

    def test_a_later_order_sees_contested_state_an_earlier_order_just_created(self):
        # Unit A attacks territory 2 first in this submission, marking
        # it contested. Unit B, starting elsewhere, then joins that same
        # fight -- demonstrating _execute_combat_moves threads each
        # order's mutation into the game_state the NEXT order sees,
        # rather than validating every order against a frozen snapshot
        # of the turn's starting board (ownership no longer flips
        # immediately on an empty capture, so this is no longer about
        # "enabling" a move that would otherwise be illegal -- both
        # orders would succeed independently too -- but the resulting
        # contested_by must still correctly reflect BOTH units' factions
        # merged from two separate calls, not just the second one's).
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 4: {'type': 'land'}},
            adjacency={1: [2], 4: [2]},
        )
        unit_a = make_unit('Infantry', 'NAA')
        unit_b = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 4: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [unit_a], 2: [defender], 4: [unit_b]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [
            CombatMoveOrder(unit_a.unit_id, [1, 2]),
            CombatMoveOrder(unit_b.unit_id, [4, 2]),
        ])
        engine.confirm_combat_moves('NAA')
        self.assertIn(unit_a, gs.territories[2].units)
        self.assertIn(unit_b, gs.territories[2].units)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'}, 'no duplication or loss across the two sequential calls')


class TestCombatMoveCarrierRideAlong(unittest.TestCase):
    def test_default_ride_along_with_no_order_of_its_own(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(carrier, gs.territories[2].units)
        self.assertIn(rider, gs.territories[2].units, 'a co-located rider with no order of its own flies into the attack too')
        self.assertTrue(rider.has_moved_combat)
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

    def test_swept_rider_becomes_a_real_combatant_via_gather_battle_units(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        attacker_units, defender_units = engine.gather_battle_units(2, 'NAA')
        self.assertIn(rider, attacker_units, 'presence alone is enough -- gather_battle_units needed no changes')

    def test_rider_gets_return_to_base_bookkeeping(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        rider = make_unit('Fighter', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, rider], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertEqual(rider.combat_move_origin, 1)
        self.assertEqual(rider.based_on_carrier, carrier.unit_id)

    def test_own_order_excludes_a_rider_regardless_of_submission_order(self):
        # Rider has its OWN attack target, different from the carrier's --
        # tested with the rider's order BOTH before and after the
        # carrier's in the submitted list, since combat move has no
        # valid "chaining" reading (unlike non-combat) and must exclude
        # it either way.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3]},
        )
        for rider_order_first in (True, False):
            with self.subTest(rider_order_first=rider_order_first):
                carrier = make_unit('Aircraft Carrier', 'NAA')
                rider = make_unit('Fighter', 'NAA')
                sea_defender = make_unit('Cruiser', 'AAC')
                land_defender = make_unit('Infantry', 'AAC')
                gs = make_state(
                    data, {3: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
                    units_by_territory={1: [carrier, rider], 2: [sea_defender], 3: [land_defender]},
                )
                engine = GameEngine(gs, data)
                rider_order = CombatMoveOrder(rider.unit_id, [1, 3])
                carrier_order = CombatMoveOrder(carrier.unit_id, [1, 2])
                orders = [rider_order, carrier_order] if rider_order_first else [carrier_order, rider_order]
                engine.submit_combat_moves('NAA', orders)
                engine.confirm_combat_moves('NAA')
                self.assertIn(rider, gs.territories[3].units, "the rider's own attack should stick")
                self.assertNotIn(rider, gs.territories[2].units, "not swept along despite being co-located when it started")
                self.assertIn(carrier, gs.territories[2].units)

    def test_only_air_units_are_swept_not_sea_or_land(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        escort = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, escort], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(escort, gs.territories[1].units, 'a co-located Cruiser is not a rider -- ride-along is air-only')

    def test_only_same_faction_riders_are_swept(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        ally_rider = make_unit('Fighter', 'UE')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [carrier, ally_rider], 2: [defender]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(carrier.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        self.assertIn(ally_rider, gs.territories[1].units, "an ally's aircraft isn't NAA's to sweep along")


class TestCombatMoveRollback(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.submit_combat_moves('NAA', [])  # "undo" -- don't move it after all
        engine.confirm_combat_moves('NAA')
        self.assertIn(mover, gs.territories[1].units)
        self.assertFalse(mover.has_moved_combat)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [])
        with self.assertRaises(ValueError):
            engine.confirm_combat_moves('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(1, [1, 2])])

    def test_defensive_faction_cannot_submit_combat_moves(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.DEFENSIVE, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_combat_moves('NAA', [CombatMoveOrder(1, [1, 2])])


class ScriptedRNG:
    """Test double for combat resolution: randint() pops scripted rolls
    in order; choices()/choice() always pick the first candidate,
    ignoring weights -- deterministic outcomes without needing to
    re-verify combat.py's own hit/damage math (already covered in
    test_combat.py)."""
    def __init__(self, rolls):
        self.rolls = list(rolls)

    def randint(self, a, b):
        return self.rolls.pop(0)

    def choices(self, population, weights=None, k=1):
        return [population[0]]

    def choice(self, population):
        return population[0]


class TestDeclaredBattlesAndGathering(unittest.TestCase):
    def test_declared_battles_returns_sea_before_land(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'sea'}}, adjacency={},
        )
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}, 3: {'NAA', 'AAC'}},
            units_by_territory={1: [make_unit('Infantry', 'NAA')], 3: [make_unit('Cruiser', 'NAA')]},
        )
        engine = GameEngine(gs, data)
        battles = engine.declared_battles('NAA')
        self.assertEqual(battles, [(3, 'sea'), (1, 'land')])

    def test_excludes_uncontested_territories(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={1: [make_unit('Infantry', 'NAA')]},
        )
        engine = GameEngine(gs, data)
        self.assertEqual(engine.declared_battles('NAA'), [])

    def test_excludes_territories_without_the_factions_own_units(self):
        # Contested, but by two OTHER factions entirely -- NAA has no
        # units there, so it's not NAA's battle to fight this turn.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'UE', 'AAC'}},
            units_by_territory={1: [make_unit('Infantry', 'AAC')]},
        )
        engine = GameEngine(gs, data)
        self.assertEqual(engine.declared_battles('NAA'), [])

    def test_gather_battle_units_pools_multiple_non_allied_defending_factions(self):
        attacker = make_unit('Infantry', 'NAA')
        defender_aac = make_unit('Infantry', 'AAC')
        defender_ue = make_unit('Armor', 'UE')
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'NAA', 'AAC', 'UE'}},
            units_by_territory={1: [attacker, defender_aac, defender_ue]},
        )
        engine = GameEngine(gs, data)
        attacker_units, defender_units = engine.gather_battle_units(1, 'NAA')
        self.assertEqual(attacker_units, [attacker])
        self.assertEqual({u.unit_id for u in defender_units}, {defender_aac.unit_id, defender_ue.unit_id})

    def test_gather_battle_units_excludes_allied_units_from_defender_pool(self):
        attacker = make_unit('Infantry', 'NAA')
        ally_unit = make_unit('Infantry', 'UE')
        enemy_unit = make_unit('Armor', 'AAC')
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'NAA', 'AAC', 'UE'}},
            units_by_territory={1: [attacker, ally_unit, enemy_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        attacker_units, defender_units = engine.gather_battle_units(1, 'NAA')
        self.assertEqual(attacker_units, [attacker])
        self.assertEqual(defender_units, [enemy_unit])


class TestResolveCombatEndToEnd(unittest.TestCase):
    def test_attacker_wins_removes_defender_and_stays_contested_pending_capture(self):
        # A decisive win leaves the attacker's own surviving unit right
        # there -- contested_by must STAY set (not clear) so Capture
        # Territory (a later phase) can actually see and resolve the
        # claim; only an attacker coalition with NO land/sea presence
        # left clears it immediately (see
        # test_defender_wins_removes_attacker_and_clears_contested).
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Infantry: D6, defense 5, damage 2, hp 2 -- attacker's roll of 6
        # cleanly kills the hp-2 defender in one hit; defender's roll of
        # 1 misses back.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([6, 1]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, 'defender_eliminated')
        self.assertIn(attacker, gs.territories[1].units)
        self.assertNotIn(defender, gs.territories[1].units)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_defender_wins_removes_attacker_and_clears_contested(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([1, 6]))
        self.assertEqual(results[0].outcome, 'attacker_eliminated')
        self.assertNotIn(attacker, gs.territories[1].units)
        self.assertIn(defender, gs.territories[1].units)
        self.assertIsNone(gs.territories[1].contested_by)

    def test_contested_outcome_keeps_contested_by_set(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Armor', 'NAA')
        defender = make_unit('Armor', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Armor: D8, defense 7 -- a roll of 3 misses cleanly every round
        # for both sides (below defense, not the die max) -- 3 rounds x
        # 2 rolls = 6 scripted misses, nobody dies.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([3, 3, 3, 3, 3, 3]))
        self.assertEqual(results[0].outcome, 'contested')
        self.assertIn(attacker, gs.territories[1].units)
        self.assertIn(defender, gs.territories[1].units)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_attacker_eliminated_but_an_ally_present_stays_contested(self):
        # NAA's own unit dies, but its ally UE has a unit right there too
        # (untouched by this battle -- combat.multi_party_battles: an
        # ally's units never fight in `faction`'s own resolve_combat
        # call) -- the attacking COALITION still has land presence via
        # the ally, so contested_by must stay set, not clear.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        ally_unit = make_unit('Infantry', 'UE')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, ally_unit, defender]},
        )
        gs.factions['NAA'].alliance = 'PACT'
        gs.factions['UE'].alliance = 'PACT'
        engine = GameEngine(gs, data)
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([1, 6]))
        self.assertEqual(results[0].outcome, 'attacker_eliminated')
        self.assertNotIn(attacker, gs.territories[1].units)
        self.assertIn(ally_unit, gs.territories[1].units)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_sea_decisive_win_clears_contested_even_with_attacker_presence(self):
        # Sea has no Capture Territory equivalent to later resolve a
        # decisive win into an ownership change -- unlike land, contested_by
        # clears here even though the attacker's own Cruiser survives.
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Cruiser: D10, defense 7, damage 3, hp 5 -- needs 2 clean hits to
        # kill; script attacker hitting twice (rounds 1-2) and missing
        # defender every round.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([10, 3, 10, 3]))
        self.assertEqual(results[0].outcome, 'defender_eliminated')
        self.assertIn(attacker, gs.territories[1].units)
        self.assertIsNone(gs.territories[1].contested_by)

    def test_air_only_survivors_clear_contested_even_on_a_decisive_win(self):
        # Attacker wins decisively (defender wiped) but only a Fighter
        # survives on the attacker's side -- air alone can't hold a
        # claim (same standard as Capture Territory), so contested_by
        # clears immediately even though the battle was won outright.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker_air = make_unit('Fighter', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker_air, defender]},
        )
        engine = GameEngine(gs, data)
        # Fighter: D8, damage 3, hp 2 -- one clean hit kills the hp-2
        # defender; defender's Infantry (D6) misses back.
        results = engine.resolve_combat('NAA', rng=ScriptedRNG([8, 1]))
        self.assertEqual(results[0].outcome, 'defender_eliminated')
        self.assertIn(attacker_air, gs.territories[1].units)
        self.assertIsNone(gs.territories[1].contested_by)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')

    def test_already_resolved_this_turn_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION)
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA')
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.NEUTRAL}, phase=Phase.COMBAT_RESOLUTION)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.resolve_combat('NAA')


class TestCombatResolutionThenCaptureTerritory(unittest.TestCase):
    """End-to-end through the real phase sequence (Combat Move -> Combat
    Resolution -> Capture Territory) -- catches an actual bug found this
    session: _apply_battle_outcome used to unconditionally clear
    contested_by after any decisive result, which meant
    process_capture_territory's own `faction not in t.contested_by`
    check silently skipped every decisive win, and ownership never
    flipped at all. Fixed by only clearing contested_by immediately when
    the attacking coalition has no land/sea presence left (see
    TestResolveCombatEndToEnd); otherwise Capture Territory is left to
    resolve it from the board state, as it always could."""
    def test_decisive_attack_on_an_undefended_territory_actually_captures_it(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}},
            adjacency={1: [2], 2: [1]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')

        gs.phase = Phase.COMBAT_RESOLUTION
        engine.resolve_combat('NAA')
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'}, 'still pending Capture Territory')
        self.assertEqual(gs.territories[2].owner, 'AAC', 'ownership does not flip until Capture Territory')

        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[2].owner, 'NAA')
        self.assertIsNone(gs.territories[2].contested_by)

    def test_decisive_defeat_leaves_the_territory_uncontested_and_unclaimed(self):
        # Mirror case: NAA attacks and loses outright (its own unit and
        # any ally wiped) -- contested_by must clear immediately (this
        # session's new rule), and Capture Territory then has nothing to
        # process for NAA there at all.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}},
            adjacency={1: [2], 2: [1]},
        )
        mover = make_unit('Infantry', 'NAA', hp=1)
        defender = make_unit('Armor', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover], 2: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')

        gs.phase = Phase.COMBAT_RESOLUTION
        # Armor (D8, damage 4) one-shots the hp-1 Infantry; Infantry's
        # D6 roll of 1 misses back.
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 8]))
        self.assertIsNone(gs.territories[2].contested_by)

        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[2].owner, 'AAC', 'the defender held -- nothing for NAA to claim')

    def test_uncontested_after_a_wiped_attack_is_no_longer_a_legal_reinforcement_target(self):
        # Once contested_by clears (attacker+allies wiped), the
        # territory reverts to a clean, non-allied foreign one -- never
        # a legal non-combat-move destination, per
        # movement.noncombat_move_destination -- even for another of
        # NAA's own units that never took part in the battle.
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}},
            adjacency={1: [2], 2: [1, 3], 3: [2]},
        )
        mover = make_unit('Infantry', 'NAA', hp=1)
        defender = make_unit('Armor', 'AAC')
        reinforcement = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover], 2: [defender], 3: [reinforcement]},
        )
        engine = GameEngine(gs, data)
        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')

        gs.phase = Phase.COMBAT_RESOLUTION
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 8]))
        self.assertIsNone(gs.territories[2].contested_by)

        gs.phase = Phase.NONCOMBAT_MOVE
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(reinforcement.unit_id, 2)])

    def test_rounds_contested_counts_battle_resolutions_up_to_the_capture(self):
        # AAC is DEFENSIVE (has units, defends normally, but never takes
        # its own active turn) so NAA is the only active faction and
        # simply keeps cycling back to itself -- lets this test drive
        # two separate Combat Resolution calls against the SAME
        # territory (a 3-round stalemate, then a decisive win) without
        # needing to also play out AAC's turns in between.
        data = FakeData(
            territories={1: {'type': 'land', 'value': 0}, 2: {'type': 'land', 'value': 0}},
            adjacency={1: [2], 2: [1]},
        )
        mover = make_unit('Armor', 'NAA')
        defender = make_unit('Armor', 'AAC')
        defender.current_hp = 3  # one hit from Armor's 3 damage finishes it, later on
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.DEFENSIVE},
            phase=Phase.COMBAT_MOVE, units_by_territory={1: [mover], 2: [defender]},
        )
        gs.active_faction = 'NAA'
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)

        engine.submit_combat_moves('NAA', [CombatMoveOrder(mover.unit_id, [1, 2])])
        engine.confirm_combat_moves('NAA')
        gs.phase = Phase.COMBAT_RESOLUTION
        # Round 1: Armor D8/defense7 -- a roll of 3 misses cleanly every
        # time for both sides -- 3-round stalemate, nobody dies.
        engine.resolve_combat('NAA', rng=ScriptedRNG([3, 3, 3, 3, 3, 3]))
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})

        gs.phase = Phase.NONCOMBAT_MOVE
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')  # AAC's Armor still standing -- skipped, stays contested
        self.assertEqual(gs.territories[2].contested_by, {'NAA', 'AAC'})
        gs.phase = Phase.DEPLOY_INCOME
        engine.deploy_and_collect_income('NAA')
        gs.phase = Phase.DIPLOMACY
        # Not calling process_game_end_check here -- AAC being DEFENSIVE
        # (not HUMAN/BOT) means it's the only active faction, and
        # would_game_end() treats "1 active faction left" as game over,
        # which isn't what this test is about.
        engine.advance_turn()

        self.assertEqual(gs.active_faction, 'NAA')  # AAC never takes an active turn
        self.assertEqual(gs.phase, Phase.PURCHASE)
        engine.submit_purchases('NAA', [])
        engine.confirm_purchases('NAA')
        gs.phase = Phase.COMBAT_MOVE
        engine.submit_combat_moves('NAA', [])  # mover is already there -- no new order needed
        engine.confirm_combat_moves('NAA')
        gs.phase = Phase.COMBAT_RESOLUTION
        # Round 2: attacker rolls the die max (8, always hits) and kills
        # the hp-3 defender in one hit; defender misses back (3).
        engine.resolve_combat('NAA', rng=ScriptedRNG([8, 3]))
        gs.phase = Phase.CAPTURE
        engine.process_capture_territory('NAA')

        self.assertEqual(gs.territories[2].owner, 'NAA')
        self.assertEqual(len(stats.captures), 1)
        self.assertEqual(stats.captures[0]['rounds_contested'], 2)

    def test_cumulative_mpc_accumulates_across_deploy_income_calls(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.DEPLOY_INCOME, treasury={'NAA': 10},
        )
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(stats.cumulative_mpc['NAA'], 3)
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 13)

        gs.factions['NAA'].turns_taken += 1  # unrelated to income, just keeping state sane between two calls
        engine.deploy_and_collect_income('NAA')
        self.assertEqual(stats.cumulative_mpc['NAA'], 6, 'accumulates rather than overwriting')
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 16)

        report = stats.report(game_state=gs)
        self.assertIn('NAA: final MPC=16 cumulative MPC=6', report)


class TestTrueTerritoryLoss(unittest.TestCase):
    """combat.true_territory_loss (this session, per the user): attacker_
    always_solo labels the active faction "attacker" in resolve_combat
    even when it's really just DEFENDING its own ground against a
    standing contest (an enemy attacked on an earlier turn and it's the
    owner's OWN Combat Resolution phase re-fighting the stalemate) -- no
    ally needed. If that defense fails outright, the surviving non-allied
    enemy must actually take ownership, right at the end of combat
    resolution -- process_capture_territory never gets a chance to (it
    only scans territories still listed in contested_by, and combat
    resolution already clears that for this exact case). See also
    test_random_bot.py's TestPlayToCompletion.
    test_true_territory_loss_can_eliminate_the_active_faction_with_no_ally
    for the full end-to-end drive."""

    def test_ownership_transfers_to_the_surviving_enemy(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC')  # AAC's own defense, about to lose
        attacker_unit = make_unit('Infantry', 'X')  # the actual conqueror
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': FactionMode.HUMAN, 'X': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'AAC', 'X'}}, units_by_territory={1: [defender_unit, attacker_unit]},
        )
        engine = GameEngine(gs, data)
        # AAC (labeled "attacker" here, per attacker_always_solo, even
        # though it's really defending its own ground) rolls 1 -- misses
        # regardless; X (the "defender" role) rolls 5 -- a clean hit,
        # kills AAC's hp-2 Infantry outright.
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 5]))
        self.assertEqual(gs.territories[1].owner, 'X')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_recorded_as_a_capture_in_stats(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC')
        attacker_unit = make_unit('Infantry', 'X')
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': FactionMode.HUMAN, 'X': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'AAC', 'X'}}, units_by_territory={1: [defender_unit, attacker_unit]}, global_turn=9,
        )
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 5]))
        self.assertEqual(len(stats.captures), 1)
        capture = stats.captures[0]
        self.assertEqual(capture['turn'], 9)
        self.assertEqual(capture['faction'], 'X')
        self.assertEqual(capture['territory_id'], 1)
        self.assertEqual(capture['previous_owner'], 'AAC')

    def test_strongest_non_ally_wins_when_more_than_one_is_present(self):
        # X and Z are both non-allied to AAC (and not allied with each
        # other either -- combat.multi_party_battles pools them as one
        # side regardless) -- Z's two Infantry outweigh X's one, so Z
        # takes the territory, not X.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC')
        x_unit = make_unit('Infantry', 'X')
        z_unit1 = make_unit('Infantry', 'Z')
        z_unit2 = make_unit('Infantry', 'Z')
        gs = make_state(
            data, {1: 'AAC'},
            {'AAC': FactionMode.HUMAN, 'X': FactionMode.HUMAN, 'Z': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'AAC', 'X', 'Z'}},
            units_by_territory={1: [defender_unit, x_unit, z_unit1, z_unit2]},
        )
        engine = GameEngine(gs, data)
        # Round 1: AAC's single roll (1) and all three defenders' rolls
        # (1, 1, 1) are safe misses -- nobody dies, continue to round 2.
        # Round 2: AAC rolls again (1, still irrelevant), then the first
        # defender unit rolls 5 -- a clean hit, kills AAC's hp-2
        # Infantry; the other two defenders' rolls (1, 1) don't matter.
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 1, 1, 1, 1, 5, 1, 1]))
        self.assertEqual(gs.territories[1].owner, 'Z')

    def test_no_transfer_when_nobody_has_land_presence(self):
        # A mutual wipe -- the ground is abandoned, not captured;
        # ownership stays with AAC by default (same "air can't capture,
        # nobody to hand it to" standard used elsewhere).
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender_unit = make_unit('Infantry', 'AAC', hp=1)
        attacker_unit = make_unit('Infantry', 'X', hp=1)
        gs = make_state(
            data, {1: 'AAC'}, {'AAC': FactionMode.HUMAN, 'X': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'AAC', 'X'}}, units_by_territory={1: [defender_unit, attacker_unit]},
        )
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        # Both roll 5 -- a clean hit against defense 5 either way,
        # killing both hp-1 units in round 1.
        engine.resolve_combat('AAC', rng=ScriptedRNG([5, 5]))
        self.assertEqual(gs.territories[1].owner, 'AAC', 'nobody holds the ground -- ownership is unchanged, not captured')
        self.assertIsNone(gs.territories[1].contested_by)
        self.assertEqual(len(stats.captures), 0)

    def test_betrayal_exception_a_failed_counter_attack_does_not_transfer_ownership(self):
        # Confirmed by the user this session: "having troops on enemy
        # lands is an exception to transferring ownership... the
        # contestation can only be cleared during the betrayer's [own]
        # claim territory phase." NAA is the original owner (betrayed by
        # AAC's alliance withdrawal), fighting to reclaim its own
        # territory -- a single failed attempt must NOT hand it to AAC.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        naa_unit = make_unit('Infantry', 'NAA')  # dies this round
        aac_unit = make_unit('Infantry', 'AAC')  # survives -- the betrayer, occupying
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [naa_unit, aac_unit]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        # NAA gets the round-1 attacker bonus here too (reclaim_bonus_for
        # == NAA) -- its own roll of 1 misses regardless either way.
        # AAC needs the D6 die-max roll (6, an auto-hit bypass) to kill
        # NAA's boosted-defense Infantry outright despite that bonus --
        # the point of this test is what happens to OWNERSHIP once NAA
        # still loses, not whether the bonus can be overcome.
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 6]))
        self.assertEqual(gs.territories[1].owner, 'NAA', 'ownership must not transfer to the betrayer')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'}, 'left completely untouched -- still open for another attempt')
        self.assertEqual(gs.territories[1].reclaim_bonus_for, 'NAA', 'the reclaim bonus is still queued for next time')

    def test_betrayal_exception_the_betrayers_own_failed_attack_ends_the_contest_normally(self):
        # Mirror case: AAC (the betrayer) attacks and its own attempt
        # fails outright -- NAA never lost this territory to begin with
        # (t.owner was never AAC's), so this is just an ordinary failed
        # attack ending the contest -- but the reclaim situation is also
        # genuinely over at that point (NAA holds it free and clear), so
        # reclaim_bonus_for clears here too, not just via
        # process_capture_territory.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        aac_unit = make_unit('Infantry', 'AAC', hp=1)  # dies
        naa_unit = make_unit('Infantry', 'NAA')  # survives
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [aac_unit, naa_unit]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        # AAC (attacker) rolls 1 -- misses; NAA (defender) rolls 5 --
        # a clean hit, kills AAC's hp-1 Infantry.
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 5]))
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)
        self.assertIsNone(gs.territories[1].reclaim_bonus_for, 'the reclaim contest is genuinely over -- NAA holds it free and clear')

    def test_betrayers_own_capture_territory_phase_finally_claims_it(self):
        # NAA never managed any presence here -- AAC's own Capture
        # Territory phase is the only place this can actually resolve in
        # AAC's favor, per the user.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        aac_unit = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [aac_unit]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('AAC')
        self.assertEqual(gs.territories[1].owner, 'AAC')
        self.assertIsNone(gs.territories[1].contested_by)
        self.assertIsNone(gs.territories[1].reclaim_bonus_for)

    def test_original_owner_reclaims_it_via_their_own_capture_territory_phase(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        naa_unit = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [naa_unit]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)
        self.assertIsNone(gs.territories[1].reclaim_bonus_for, 'the reclaim is done, even though ownership technically never changed')


class TestEmergencyLandingConsequence(unittest.TestCase):
    """Exercises _apply_battle_outcome/_resolve_stranded_defender_aircraft
    directly against a hand-built BattleResult -- keeps this focused on
    the engine-level consequence logic without re-driving combat.py's
    own dice math (covered in test_combat.py)."""

    def test_surviving_defender_aircraft_relocated_when_own_carrier_destroyed(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        carrier = make_unit('Aircraft Carrier', 'AAC')
        fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [fighter]},  # carrier already removed by _apply_battle_outcome's dead-unit cleanup
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[fighter.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[carrier.unit_id],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertNotIn(fighter, gs.territories[2].units)
        self.assertIn(fighter, gs.territories[1].units, "AAC's own land is the only qualifying emergency landing spot")

    def test_surviving_defender_aircraft_stays_when_own_carrier_survives(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        carrier = make_unit('Aircraft Carrier', 'AAC')
        fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [carrier, fighter]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[carrier.unit_id, fighter.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertIn(fighter, gs.territories[2].units, "the carrier survived -- no emergency landing needed")

    def test_attacker_aircraft_unaffected_by_emergency_landing(self):
        # Attacker's own carrier destroyed -- defender-only mechanic, so
        # the attacker's surviving Fighter should be left exactly where it is.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'sea'}}, adjacency={2: [1]})
        attacker_carrier = make_unit('Aircraft Carrier', 'NAA')
        attacker_fighter = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={2: [attacker_fighter]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[attacker_fighter.unit_id], surviving_defender_ids=[],
            eliminated_attacker_ids=[attacker_carrier.unit_id], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(2, 'sea', 'NAA', result, random.Random(1))
        self.assertIn(attacker_fighter, gs.territories[2].units)

    def test_land_battle_never_triggers_emergency_landing_check(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        survivor = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            units_by_territory={1: [survivor]},
        )
        result = BattleResult(
            outcome='contested', rounds_fought=3,
            surviving_attacker_ids=[], surviving_defender_ids=[survivor.unit_id],
            eliminated_attacker_ids=[], eliminated_defender_ids=[],
        )
        engine = GameEngine(gs, data)
        engine._apply_battle_outcome(1, 'land', 'NAA', result, random.Random(1))
        self.assertIn(survivor, gs.territories[1].units)


class TestNonCombatMoveExecution(unittest.TestCase):
    def test_simple_move_to_own_territory(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(mover, gs.territories[1].units)
        self.assertIn(mover, gs.territories[2].units)
        self.assertTrue(mover.has_moved_noncombat)

    def test_entering_a_territory_the_mover_is_already_contesting_is_legal(self):
        # NAA is already fighting for territory 2 (its own combat-moved
        # unit is there) -- reinforcing via a non-combat move needs no
        # fresh attack declaration.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        reinforcement = make_unit('Infantry', 'NAA')
        already_there = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            contested={2: {'NAA', 'AAC'}},
            units_by_territory={1: [reinforcement], 2: [already_there, defender]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(reinforcement.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(reinforcement, gs.territories[2].units)

    def test_entering_a_territory_contested_by_others_only_is_also_legal(self):
        # Contested between two OTHER factions entirely -- NAA isn't
        # part of it yet, but can still walk in via non-combat move.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN},
            phase=Phase.NONCOMBAT_MOVE, contested={2: {'AAC', 'UE'}}, units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)

    def test_cannot_noncombat_move_after_already_combat_moving(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])

    def test_air_can_noncombat_move_after_already_combat_moving(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Fighter', 'NAA')
        mover.has_moved_combat = True
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[2].units)

    def test_illegal_destination_is_rejected(self):
        # Clean (uncontested), non-allied foreign land -- never a legal
        # non-combat move target.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])

    def test_cannot_move_the_same_unit_twice(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'land'}, 3: {'type': 'land'}}, adjacency={1: [2], 2: [1, 3]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA', 3: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [
                NonCombatMoveOrder(mover.unit_id, 2),
                NonCombatMoveOrder(mover.unit_id, 3),
            ])


class TestNonCombatMoveRollback(unittest.TestCase):
    def test_resubmitting_replaces_the_staged_list(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.submit_noncombat_moves('NAA', [])  # "undo"
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[1].units)
        self.assertFalse(mover.has_moved_noncombat)

    def test_cannot_resubmit_after_confirming(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [mover]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [])
        with self.assertRaises(ValueError):
            engine.confirm_noncombat_moves('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(1, 2)])

    def test_defensive_faction_cannot_submit_noncombat_moves(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.DEFENSIVE}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(1, 2)])

    def test_submit_noncombat_moves_requires_return_to_base_first(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        gs = make_state(data, {1: 'NAA', 2: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.submit_noncombat_moves('NAA', [])


class TestReturnToBase(unittest.TestCase):
    def test_land_takeoff_returns_to_origin_territory(self):
        # Fighter attacked from territory 1 (still NAA's) and is now
        # sitting at territory 2 (its attack destination) after combat.
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertNotIn(flyer, gs.territories[2].units)
        self.assertIn(flyer, gs.territories[1].units)
        self.assertTrue(flyer.has_moved_noncombat)
        self.assertIsNone(flyer.combat_move_origin)

    def test_return_to_base_is_logged_with_where_the_unit_went_from_and_to(self):
        data = FakeData(territories={1: {'type': 'land'}, 2: {'type': 'land'}}, adjacency={1: [2]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer]},
        )
        log = TurnLog()
        GameEngine(gs, data, turn_log=log).process_return_to_base('NAA')
        self.assertEqual(log.events, [{
            'kind': 'return_to_base', 'faction': 'NAA',
            'orders': [{'unit_id': flyer.unit_id, 'unit_type': 'Fighter', 'from': 2, 'to': 1}],
        }])

    def test_carrier_takeoff_returns_to_carrier_wherever_it_now_is(self):
        # Flyer departed carrier's original zone (territory 1). The
        # carrier has SINCE moved (its own order, already applied) to
        # territory 3. The flyer should return to territory 3, not 1.
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'land'}, 3: {'type': 'sea'}}, adjacency={},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        flyer.based_on_carrier = carrier.unit_id
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer], 3: [carrier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertIn(flyer, gs.territories[3].units)
        self.assertTrue(flyer.has_moved_noncombat)

    def test_carrier_destroyed_falls_through_to_regular_move(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'land'}}, adjacency={2: [1]})
        flyer = make_unit('Fighter', 'NAA')
        flyer.combat_move_origin = 1
        flyer.based_on_carrier = 9999  # no unit anywhere has this id -- destroyed
        gs = make_state(
            data, {2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={2: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertIn(flyer, gs.territories[2].units, 'left exactly where it was -- no auto-relocation possible')
        self.assertFalse(flyer.has_moved_noncombat, 'free to receive a normal non-combat move order instead')
        self.assertIsNone(flyer.combat_move_origin, 'the return-to-base attempt is still consumed either way')

    def test_units_without_a_combat_move_this_turn_are_unaffected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        untouched = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [untouched]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        self.assertFalse(untouched.has_moved_noncombat)

    def test_cannot_process_return_to_base_twice(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.NEUTRAL}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_return_to_base('NAA')


class TestCarrierRideAlong(unittest.TestCase):
    def test_default_ride_along_with_no_order_of_its_own(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(carrier.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(carrier, gs.territories[2].units)
        self.assertIn(flyer, gs.territories[2].units, 'co-located plane with no order of its own rides along')
        self.assertTrue(flyer.has_moved_noncombat)

    def test_preemption_a_planes_own_earlier_move_excludes_it_from_the_ride(self):
        data = FakeData(
            territories={1: {'type': 'sea'}, 2: {'type': 'sea'}, 3: {'type': 'land'}},
            adjacency={1: [2, 3], 3: [1]},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {3: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [
            NonCombatMoveOrder(flyer.unit_id, 3),  # plane's own move, processed first -- flies to friendly land
            NonCombatMoveOrder(carrier.unit_id, 2),  # carrier's own move, processed after
        ])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[3].units, "the plane's own move should stick, not get dragged to the carrier's zone")
        self.assertIn(carrier, gs.territories[2].units)

    def test_chaining_a_plane_that_flies_onto_the_carrier_then_rides_its_later_move(self):
        data = FakeData(
            territories={1: {'type': 'land'}, 2: {'type': 'sea'}, 3: {'type': 'sea'}},
            adjacency={1: [2], 2: [1, 3]},
        )
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [flyer], 2: [carrier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [
            NonCombatMoveOrder(flyer.unit_id, 2),  # plane flies onto the carrier's CURRENT zone, using its own move
            NonCombatMoveOrder(carrier.unit_id, 3),  # carrier then moves on -- plane should ride along for free
        ])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[3].units, "chaining: the plane should ride the carrier's later move too")
        self.assertIn(carrier, gs.territories[3].units)

    def test_only_same_faction_air_units_are_swept_along(self):
        data = FakeData(territories={1: {'type': 'sea'}, 2: {'type': 'sea'}}, adjacency={1: [2]})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        own_flyer = make_unit('Fighter', 'NAA')
        ally_flyer = make_unit('Fighter', 'UE')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, own_flyer, ally_flyer]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(carrier.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(own_flyer, gs.territories[2].units)
        self.assertIn(ally_flyer, gs.territories[1].units, "an ally's aircraft isn't swept along by NAA's carrier order")


class TestStrandedAircraftCheck(unittest.TestCase):
    def test_aircraft_without_own_carrier_is_lost(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(flyer, gs.territories[1].units)

    def test_aircraft_with_own_carrier_survives(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        carrier = make_unit('Aircraft Carrier', 'NAA')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [carrier, flyer]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(flyer, gs.territories[1].units)

    def test_allied_carrier_does_not_save_a_stranded_aircraft(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        ally_carrier = make_unit('Aircraft Carrier', 'UE')
        flyer = make_unit('Fighter', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [ally_carrier, flyer]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertNotIn(flyer, gs.territories[1].units, "an ally's carrier doesn't count -- only your own")

    def test_land_units_are_never_stranded(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        soldier = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE,
            units_by_territory={1: [soldier]},
        )
        engine = GameEngine(gs, data)
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [])
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(soldier, gs.territories[1].units)


class TestCaptureTerritory(unittest.TestCase):
    def test_claims_unoccupied_territory_ran_through(self):
        # Nobody's land units are physically there -- faction passed
        # through and kept moving -- but it's still owed the claim.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_claims_territory_with_only_own_land_units_remaining(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        survivor = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [survivor]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_leaves_ownership_unchanged_when_enemy_land_units_remain(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [defender]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'AAC', 'still genuinely contested -- ownership stays put')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'})

    def test_air_only_survivors_do_not_block_the_claim(self):
        # The defender's only survivor is a Fighter -- air can't hold ground.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        surviving_fighter = make_unit('Fighter', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [surviving_fighter]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA')
        self.assertIsNone(gs.territories[1].contested_by)

    def test_allied_land_units_do_not_block_the_claim(self):
        # NAA has its OWN land unit here too, so per "if you have one
        # land unit, you can keep it," NAA wins even though its ally is
        # also present -- an ally's presence never BLOCKS faction's own claim.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        own_unit = make_unit('Infantry', 'NAA')
        ally_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [own_unit, ally_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'NAA', "an ally's land presence doesn't block faction's own claim")

    def test_ally_claims_the_territory_when_faction_has_no_land_units_left(self):
        # Confirmed this session: if faction's OWN land units were all
        # eliminated but an ally's weren't, the ALLY claims it -- even
        # though it's still faction's own Capture Territory phase doing
        # the awarding.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ally_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [ally_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'UE', "the ally claims it, not NAA")

    def test_multiple_allies_present_greatest_land_cost_wins(self):
        # UE has one Armor (cost 8); GPC has two Infantry (cost 4 each =
        # 8 total)... make them clearly unequal: UE gets an Armor (8),
        # GPC gets a single Infantry (4) -- UE should win.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ue_unit = make_unit('Armor', 'UE')
        gpc_unit = make_unit('Infantry', 'GPC')
        gs = make_state(
            data, {1: 'AAC'},
            {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.CAPTURE, contested={1: {'NAA', 'AAC', 'UE', 'GPC'}},
            units_by_territory={1: [ue_unit, gpc_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['GPC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'UE', 'Armor (cost 8) beats a single Infantry (cost 4)')

    def test_multiple_allies_tied_on_cost_turn_order_breaks_the_tie(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        ue_unit = make_unit('Infantry', 'UE')
        gpc_unit = make_unit('Infantry', 'GPC')
        gs = make_state(
            data, {1: 'AAC'},
            {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.CAPTURE, contested={1: {'NAA', 'AAC', 'UE', 'GPC'}},
            units_by_territory={1: [ue_unit, gpc_unit]},
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['GPC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        # tied on cost (one Infantry each) -- UE was declared to
        # make_state before GPC, so UE comes first in GameState.factions'
        # iteration order and should win the tie.
        self.assertEqual(gs.territories[1].owner, 'UE', 'tied on cost -- earlier turn order wins')

    def test_two_other_factions_still_contesting_leaves_ownership_alone(self):
        # Neither AAC nor UE is allied with NAA (or each other); both
        # still have land units present -- even the nominal owner being
        # a THIRD, unrelated faction (simulating one that's since been
        # eliminated) shouldn't matter -- nothing resolves in NAA's
        # favor while two other factions are still genuinely contesting.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        aac_unit = make_unit('Infantry', 'AAC')
        ue_unit = make_unit('Infantry', 'UE')
        gs = make_state(
            data, {1: 'PAF'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC', 'UE'}}, units_by_territory={1: [aac_unit, ue_unit]},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'PAF', 'original ownership maintained regardless of NAA')
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC', 'UE'})

    def test_faction_not_in_contested_by_is_untouched(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'AAC', 'UE'}},  # NAA has no stake in this one
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')
        self.assertEqual(gs.territories[1].owner, 'AAC')
        self.assertEqual(gs.territories[1].contested_by, {'AAC', 'UE'})

    def test_sea_territories_are_never_touched(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE,
            contested={1: {'NAA', 'AAC'}},
        )
        engine = GameEngine(gs, data)
        engine.process_capture_territory('NAA')  # should not raise
        self.assertIsNone(gs.territories[1].owner)
        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'AAC'}, 'sea contested status is Combat Resolution\'s concern, not this phase\'s')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.NONCOMBAT_MOVE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_capture_territory('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'NAA': FactionMode.NEUTRAL, 'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_capture_territory('NAA')


class TestSurrender(unittest.TestCase):
    """Nobody is eliminated for being down to 0-1 Strategic Centers any more: a faction leaves the
    game when another forces its surrender in the Diplomacy phase -- because the demander's
    income (the value of its uncontested territories) is more than 200% of the target's, or the target
    holds 0-1 Strategic Centers and the demander controls one that was originally the target's."""

    def rich_and_poor(self, **kw):
        # NAA: value 5 + 3 + 2 = 10 (uncontested); AAC: value 1 + 1 = 2 -- more than double.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 5, 'faction': 'NAA'}, 2: {'type': 'land', 'value': 3, 'faction': 'NAA'},
                3: {'type': 'land', 'value': 2, 'faction': 'NAA'},
                4: {'type': 'land', 'value': 1, 'faction': 'AAC'}, 5: {'type': 'land', 'value': 1, 'faction': 'AAC'},
            },
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA', 3: 'NAA', 4: 'AAC', 5: 'AAC'},
                        {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY, **kw)
        gs.active_faction = 'NAA'
        return data, gs, GameEngine(gs, data)

    def test_nobody_is_eliminated_for_holding_no_strategic_centers(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 3}}, adjacency={})
        gs = make_state(data, {1: 'AAC'}, {'AAC': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        gs.active_faction = 'AAC'
        engine = GameEngine(gs, data)
        engine.process_capture_territory('AAC')
        self.assertFalse(gs.factions['AAC'].eliminated)
        self.assertFalse(hasattr(engine, 'process_elimination_check'))

    def test_more_than_double_the_income_is_grounds(self):
        _, _, engine = self.rich_and_poor()
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), ['income'])
        self.assertEqual(engine.surrender_grounds('AAC', 'NAA'), [])

    def test_exactly_double_is_not_enough(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 4}, 2: {'type': 'land', 'value': 2}}, adjacency={})
        gs = make_state(data, {1: 'NAA', 2: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        self.assertEqual(GameEngine(gs, data).surrender_grounds('NAA', 'AAC'), [])

    def test_contested_territory_is_left_out_of_the_income(self):
        # NAA's 5- and 3-value territories are contested: only the 2 is left, which is not more than double AAC's 2.
        _, _, engine = self.rich_and_poor(contested={1: {'NAA', 'UE'}, 2: {'NAA', 'UE'}})
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), [])

    def test_a_captured_strategic_center_of_a_faction_with_one_or_none_is_grounds(self):
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 1, 'strategic_center': True, 'faction': 'AAC'},   # AAC's, held by NAA
                2: {'type': 'land', 'value': 3, 'strategic_center': True, 'faction': 'NAA'},
                3: {'type': 'land', 'value': 3, 'strategic_center': True, 'faction': 'AAC'},   # AAC's own last one
                4: {'type': 'land', 'value': 3, 'faction': 'AAC'},
            },
            adjacency={},
        )
        gs = make_state(data, {1: 'NAA', 2: 'NAA', 3: 'AAC', 4: 'AAC'},
                        {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), ['strategic_center'])
        gs.territories[4].owner = 'NAA'
        gs.territories[3].owner = 'NAA'   # now AAC has none left
        self.assertIn('strategic_center', engine.surrender_grounds('NAA', 'AAC'))

    def test_two_strategic_centers_are_safe_and_a_stranger_s_center_is_no_grounds(self):
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 1, 'strategic_center': True, 'faction': 'AAC'},
                2: {'type': 'land', 'value': 1, 'strategic_center': True, 'faction': 'AAC'},
                3: {'type': 'land', 'value': 1, 'strategic_center': True, 'faction': 'UE'},   # not AAC's
                4: {'type': 'land', 'value': 1, 'strategic_center': True, 'faction': 'AAC'},
            },
            adjacency={},
        )
        gs = make_state(data, {1: 'AAC', 2: 'AAC', 3: 'NAA', 4: 'NAA'},
                        {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), [])  # AAC still holds two
        gs.territories[2].owner = 'UE'
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), ['strategic_center'])  # one left, and NAA has 4 (AAC's)
        gs.territories[4].owner = 'AAC'
        self.assertEqual(engine.surrender_grounds('NAA', 'AAC'), [])  # AAC took its own back: out of risk

    def test_the_demand_eliminates_and_clears_the_board(self):
        data, gs, engine = self.rich_and_poor(
            units_by_territory={4: [make_unit('Infantry', 'AAC')], 1: [make_unit('Infantry', 'NAA')]},
            pending_by_territory={5: [make_unit('Infantry', 'AAC')]})
        log = TurnLog()
        engine.turn_log = log
        reasons = engine.demand_surrender('NAA', 'AAC')
        self.assertEqual(reasons, ['income'])
        self.assertTrue(gs.factions['AAC'].eliminated)
        self.assertNotIn('AAC', gs.active_factions())
        self.assertEqual([u.owner for t in gs.territories.values() for u in t.units], ['NAA'])
        self.assertEqual(gs.territories[5].pending_deployment, [])
        self.assertEqual(gs.territories[4].owner, 'AAC')  # its land stays as it is
        kinds = [e['kind'] for e in log.events]
        self.assertEqual(kinds, ['surrender', 'faction_eliminated'])
        self.assertEqual(log.events[0]['reasons'], ['income'])

    def test_a_demand_without_grounds_or_out_of_turn_is_refused(self):
        _, gs, engine = self.rich_and_poor()
        with self.assertRaises(ValueError):
            engine.demand_surrender('AAC', 'NAA')
        with self.assertRaises(ValueError):
            engine.demand_surrender('NAA', 'NAA')
        gs.phase = Phase.CAPTURE
        with self.assertRaises(ValueError):
            engine.demand_surrender('NAA', 'AAC')
        gs.phase = Phase.DIPLOMACY
        gs.active_faction = 'AAC'
        with self.assertRaises(ValueError):
            engine.demand_surrender('NAA', 'AAC')

    def test_neutral_and_defensive_factions_are_never_targets(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 5}, 2: {'type': 'land', 'value': 1}, 3: {'type': 'land', 'value': 1}}, adjacency={})
        gs = make_state(data, {1: 'NAA', 2: 'PAF', 3: 'UE'},
                        {'NAA': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL, 'UE': FactionMode.DEFENSIVE}, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        self.assertEqual(engine.legal_surrender_targets('NAA'), [])
        with self.assertRaises(ValueError):
            engine.demand_surrender('NAA', 'PAF')

    def test_allies_can_be_demanded_and_are_flagged(self):
        _, gs, engine = self.rich_and_poor()
        gs.factions['NAA'].alliance = gs.factions['AAC'].alliance = 'pact'
        targets = engine.legal_surrender_targets('NAA')
        self.assertEqual([(t['target'], t['allied'], t['reasons']) for t in targets], [('AAC', True, ['income'])])
        self.assertEqual(targets[0]['income'], {'yours': 10, 'theirs': 2})

    def test_eliminating_a_member_cuts_its_ties_and_a_lone_alliance_dissolves(self):
        data = FakeData(
            territories={i: {'type': 'land', 'value': v, 'faction': f} for i, v, f in
                         [(1, 6, 'NAA'), (2, 1, 'AAC'), (3, 1, 'UE')]},
            adjacency={})
        gs = make_state(data, {1: 'NAA', 2: 'AAC', 3: 'UE'},
                        {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        gs.factions['AAC'].alliance = gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.demand_surrender('NAA', 'AAC')
        self.assertIsNone(gs.factions['AAC'].alliance)
        self.assertIsNone(gs.factions['UE'].alliance)  # an alliance of one is no alliance

    def test_the_last_demand_ends_the_game(self):
        _, gs, engine = self.rich_and_poor()
        engine.demand_surrender('NAA', 'AAC')
        self.assertTrue(engine.would_game_end())
        self.assertTrue(engine.process_game_end_check('NAA'))


class TestGameEndCheck(unittest.TestCase):
    def test_would_game_end_true_with_a_single_active_faction(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        self.assertTrue(engine.would_game_end())

    def test_would_game_end_true_when_all_active_factions_share_an_alliance(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        self.assertTrue(engine.would_game_end())

    def test_would_game_end_false_when_non_allied_factions_remain(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        self.assertFalse(engine.would_game_end())

    def test_would_game_end_false_with_two_allied_and_one_outsider(self):
        # NAA+UE share an alliance, but AAC doesn't -- not EVERYONE is
        # mutually allied, so there's still someone to fight.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        self.assertFalse(engine.would_game_end())

    def test_eliminated_and_neutral_factions_dont_count(self):
        # AAC is eliminated, PAF is Neutral -- neither ever takes turns,
        # so with NAA and UE (allied) as the only ACTIVE factions left,
        # the game should still be considered over.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'PAF': FactionMode.NEUTRAL},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].eliminated = True
        engine = GameEngine(gs, data)
        self.assertTrue(engine.would_game_end())

    def test_process_game_end_check_sets_game_over(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        result = engine.process_game_end_check('NAA')
        self.assertTrue(result)
        self.assertTrue(gs.game_over)

    def test_withdrawing_during_the_regular_alliance_action_keeps_the_game_going(self):
        # There's no separate "last chance" window -- withdrawing to
        # avoid game-over is just the faction's regular Alliances-phase
        # action (withdraw_from_alliance), same as any other turn,
        # called BEFORE process_game_end_check.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')
        result = engine.process_game_end_check('NAA')
        self.assertFalse(result)
        self.assertFalse(gs.game_over)
        self.assertIsNone(gs.factions['NAA'].alliance)
        # A pair's other member is freed too: an alliance of one is no alliance (see
        # withdraw_from_alliance), so UE can be invited again, and can invite.
        self.assertIsNone(gs.factions['UE'].alliance)

    def test_not_withdrawing_lets_the_game_end(self):
        # Confirms there's no automatic/implicit save here -- if the
        # faction didn't withdraw during its own regular turn, the game
        # simply ends.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        result = engine.process_game_end_check('NAA')
        self.assertTrue(result)
        self.assertTrue(gs.game_over)
        self.assertEqual(gs.factions['NAA'].alliance, 'pact')

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_game_end_check('NAA')

    def test_non_active_faction_is_rejected(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.NEUTRAL}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.process_game_end_check('NAA')


class TestLegalAllianceOptions(unittest.TestCase):
    """GameEngine.legal_alliance_options -- the "get the full list of
    legal options" query Alliances as a real human decision point is
    built on, mirroring legal_combat_move_options/legal_noncombat_move_
    options for a per-faction rather than per-unit decision."""

    def test_excludes_self(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        options = engine.legal_alliance_options('NAA')
        self.assertNotIn('NAA', options['eligible_invite_targets'])
        self.assertIn('UE', options['eligible_invite_targets'])

    def test_excludes_a_faction_already_in_another_alliance(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        options = engine.legal_alliance_options('NAA')
        self.assertEqual(options['eligible_invite_targets'], [])

    def test_excludes_own_existing_allies_but_includes_others(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        options = engine.legal_alliance_options('NAA')
        self.assertNotIn('UE', options['eligible_invite_targets'], 'already an ally -- nothing to invite')
        self.assertIn('AAC', options['eligible_invite_targets'])

    def test_excludes_former_allies_when_rejoining_disabled(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.can_rejoin_alliances = False
        gs.factions['NAA'].former_allies = {'UE'}
        gs.factions['UE'].former_allies = {'NAA'}
        engine = GameEngine(gs, data)
        options = engine.legal_alliance_options('NAA')
        self.assertEqual(options['eligible_invite_targets'], [])

    def test_does_not_filter_by_effective_max_alliance_size(self):
        # Unlike a unit's own destination list, the candidate pool here
        # isn't narrowed by _effective_max_alliance_size -- invite_to_
        # alliance is still the authority on whether a SPECIFIC pick
        # would exceed it.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 1  # NAA+UE would already be at the cap
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        options = engine.legal_alliance_options('NAA')
        self.assertIn('AAC', options['eligible_invite_targets'])
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)

    def test_can_withdraw_true_when_allied_and_allowed(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        self.assertTrue(engine.legal_alliance_options('NAA')['can_withdraw'])

    def test_can_withdraw_false_when_not_allied(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        self.assertFalse(engine.legal_alliance_options('NAA')['can_withdraw'])

    def test_can_withdraw_false_when_disabled_by_setting(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.can_withdraw_from_alliances = False
        engine = GameEngine(gs, data)
        self.assertFalse(engine.legal_alliance_options('NAA')['can_withdraw'])

    def test_can_withdraw_false_when_blocked_by_sc_lock(self):
        data = FakeData(territories={1: {'type': 'land', 'strategic_center': True}}, adjacency={})
        gs = make_state(
            data, {1: 'UE'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        engine = GameEngine(gs, data)
        self.assertFalse(engine.legal_alliance_options('NAA')['can_withdraw'])


class TestInviteToAlliance(unittest.TestCase):
    def test_creates_a_new_alliance_when_inviter_has_none(self):
        # A bystander faction (PAF) is required here: with only 2 active
        # factions, _effective_max_alliance_size() would cap out at 1
        # (active_count - 1), since an alliance can never include every
        # remaining active faction -- see TestEffectiveMaxAllianceSize.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        engine = GameEngine(gs, data)
        accepted = engine.invite_to_alliance('NAA', 'UE', target_accepts=True)
        self.assertTrue(accepted)
        self.assertIsNotNone(gs.factions['NAA'].alliance)
        self.assertEqual(gs.factions['NAA'].alliance, gs.factions['UE'].alliance)

    def test_adds_to_an_existing_alliance(self):
        # A 2nd bystander (GPC) alongside AAC: 4 active factions gives an
        # effective cap of min(3, 4-1) = 3, matching the prospective
        # 3-member alliance below -- see test_creates_a_new_alliance_when_
        # inviter_has_none's comment.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.max_alliance_size = 3
        engine = GameEngine(gs, data)
        accepted = engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)
        self.assertTrue(accepted)
        self.assertEqual(gs.factions['AAC'].alliance, 'pact')

    def test_decline_leaves_state_unchanged(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        engine = GameEngine(gs, data)
        accepted = engine.invite_to_alliance('NAA', 'UE', target_accepts=False)
        self.assertFalse(accepted)
        self.assertIsNone(gs.factions['NAA'].alliance)
        self.assertIsNone(gs.factions['UE'].alliance)

    def test_cannot_invite_self(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'NAA', target_accepts=True)

    def test_cannot_invite_a_neutral_faction(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.NEUTRAL}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_cannot_invite_a_defensive_faction(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.DEFENSIVE}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_cannot_invite_an_already_allied_faction(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_max_alliance_size_blocks_the_invite(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.max_alliance_size = 2
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)

    def test_rejoin_banned_by_default(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].former_allies.add('UE')
        gs.factions['UE'].former_allies.add('NAA')
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_rejoin_allowed_when_setting_is_true(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].former_allies.add('UE')
        gs.factions['UE'].former_allies.add('NAA')
        gs.can_rejoin_alliances = True
        engine = GameEngine(gs, data)
        accepted = engine.invite_to_alliance('NAA', 'UE', target_accepts=True)
        self.assertTrue(accepted)

    def test_rejoin_ban_checks_the_whole_prospective_alliance_not_just_the_inviter(self):
        # NAA already allied with GPC. UE was previously allied with GPC
        # and withdrew -- UE can't join NAA's alliance even though UE
        # was never directly allied with NAA, because GPC (a current
        # member of the alliance UE would be joining) is in UE's
        # former_allies. A 4th faction (PAF) bystander keeps the
        # effective size cap (min(3, active-1)) from being what actually
        # blocks this invite, so it's really the rejoin ban being tested.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['GPC'].alliance = 'pact'
        gs.max_alliance_size = 3
        gs.factions['UE'].former_allies.add('GPC')
        gs.factions['GPC'].former_allies.add('UE')
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

    def test_records_a_new_alliance_forming_in_stats(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY, global_turn=7,
        )
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.invite_to_alliance('NAA', 'UE', target_accepts=True)

        self.assertEqual(len(stats.alliance_changes), 1)
        change = stats.alliance_changes[0]
        self.assertEqual(change['turn'], 7)
        self.assertEqual(change['kind'], 'joined')
        self.assertTrue(change['new_alliance'])
        self.assertEqual(change['faction'], 'NAA')
        self.assertEqual(change['target'], 'UE')

    def test_records_joining_an_existing_alliance_as_not_new(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.max_alliance_size = 3
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)
        self.assertFalse(stats.alliance_changes[0]['new_alliance'])

    def test_declined_invite_is_not_recorded_in_stats(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'PAF': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.invite_to_alliance('NAA', 'UE', target_accepts=False)
        self.assertEqual(stats.alliance_changes, [])

    def test_one_alliance_action_per_turn(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        engine = GameEngine(gs, data)
        engine.invite_to_alliance('NAA', 'UE', target_accepts=True)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)


class TestEffectiveMaxAllianceSize(unittest.TestCase):
    """game_start_settings.max_alliance_size is only a ceiling --
    GameEngine._effective_max_alliance_size() further caps it to
    (active faction count - 1), so an alliance can never include every
    remaining active faction (that would make forming it identical to
    ending the game -- see would_game_end()). Recomputed fresh from the
    CURRENT active-faction count on every invite, so eliminations over
    the course of the game progressively tighten it -- but never
    dissolve an alliance that's already bigger than the new value."""

    def test_capped_by_active_faction_count_when_lower_than_the_configured_setting(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 5  # configured ceiling higher than 3 active factions - 1
        engine = GameEngine(gs, data)
        self.assertEqual(engine._effective_max_alliance_size(), 2)

    def test_capped_by_the_configured_setting_when_lower_than_active_faction_count(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 2  # lower than 4 active factions - 1 = 3
        engine = GameEngine(gs, data)
        self.assertEqual(engine._effective_max_alliance_size(), 2)

    def test_shrinks_as_factions_are_eliminated(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 5
        engine = GameEngine(gs, data)
        self.assertEqual(engine._effective_max_alliance_size(), 3)  # 4 active - 1
        gs.factions['GPC'].eliminated = True
        self.assertEqual(engine._effective_max_alliance_size(), 2)  # now 3 active - 1

    def test_elimination_does_not_dissolve_an_existing_larger_alliance(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 3
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].alliance = 'pact'  # a 3-member alliance, formed while the cap allowed it
        engine = GameEngine(gs, data)

        gs.factions['GPC'].eliminated = True  # 3 active factions remain -- cap is now 2, below the alliance's size
        self.assertEqual(engine._effective_max_alliance_size(), 2)
        self.assertEqual(gs.factions['NAA'].alliance, 'pact')
        self.assertEqual(gs.factions['UE'].alliance, 'pact')
        self.assertEqual(gs.factions['AAC'].alliance, 'pact')

    def test_elimination_blocks_a_new_invite_the_configured_setting_would_otherwise_allow(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {},
            {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.max_alliance_size = 3
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].eliminated = True  # 3 active factions remain -- effective cap drops to 2
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'GPC', target_accepts=True)  # would make a 3rd member


class TestWithdrawFromAlliance(unittest.TestCase):
    def test_leaves_the_alliance_and_records_former_allies_symmetrically(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')

        self.assertIsNone(gs.factions['NAA'].alliance)
        self.assertEqual(gs.factions['UE'].alliance, 'pact')
        self.assertEqual(gs.factions['AAC'].alliance, 'pact')
        self.assertEqual(gs.factions['NAA'].former_allies, {'UE', 'AAC'})
        self.assertIn('NAA', gs.factions['UE'].former_allies)
        self.assertIn('NAA', gs.factions['AAC'].former_allies)

    def test_disabled_by_setting(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.can_withdraw_from_alliances = False
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.withdraw_from_alliance('NAA')

    def test_raises_with_no_alliance_to_leave(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.withdraw_from_alliance('NAA')

    def test_sc_lock_blocks_withdrawal(self):
        data = FakeData(territories={1: {'type': 'land', 'strategic_center': True}}, adjacency={})
        gs = make_state(
            data, {1: 'UE'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.withdraw_from_alliance('NAA')

    def test_sc_lock_does_not_block_withdrawal_from_own_sc(self):
        data = FakeData(territories={1: {'type': 'land', 'strategic_center': True}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')  # should not raise
        self.assertIsNone(gs.factions['NAA'].alliance)

    def test_occupied_former_ally_territory_becomes_contested_with_reclaim_bonus_queued(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'UE'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.territories[1].units.append(make_unit('Infantry', 'NAA'))
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')

        self.assertEqual(gs.territories[1].contested_by, {'NAA', 'UE'})
        self.assertEqual(gs.territories[1].reclaim_bonus_for, 'UE')

    def test_unoccupied_former_ally_territory_is_unaffected(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'UE'}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')

        self.assertIsNone(gs.territories[1].contested_by)
        self.assertIsNone(gs.territories[1].reclaim_bonus_for)

    def test_one_alliance_action_per_turn_is_shared_between_invite_and_withdraw(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        engine = GameEngine(gs, data)
        engine.withdraw_from_alliance('NAA')
        with self.assertRaises(ValueError):
            engine.invite_to_alliance('NAA', 'AAC', target_accepts=True)

    def test_wrong_phase_is_rejected(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        gs.factions['NAA'].alliance = 'pact'
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.withdraw_from_alliance('NAA')

    def test_records_a_withdrawal_in_stats(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.DIPLOMACY, global_turn=12,
        )
        gs.factions['NAA'].alliance = 'pact'
        gs.factions['UE'].alliance = 'pact'
        gs.factions['AAC'].alliance = 'pact'
        stats = GameStats()
        engine = GameEngine(gs, data, stats=stats)
        engine.withdraw_from_alliance('NAA')

        self.assertEqual(len(stats.alliance_changes), 1)
        change = stats.alliance_changes[0]
        self.assertEqual(change['turn'], 12)
        self.assertEqual(change['kind'], 'withdrew')
        self.assertEqual(change['faction'], 'NAA')
        self.assertEqual(change['tag'], 'pact')
        self.assertEqual(change['former_members'], ['AAC', 'UE'])


class TestGameStatsAllianceReporting(unittest.TestCase):
    def test_report_lists_alliance_policies_when_game_state_given(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.factions['NAA'].alliance_strategy = 'aggressive'
        gs.factions['NAA'].alliance_behavior = 'treacherous'
        gs.factions['UE'].alliance_strategy = 'passive'
        gs.factions['UE'].alliance_behavior = 'loyal'
        stats = GameStats()
        report = stats.report(gs)
        self.assertIn('=== Alliance Policies ===', report)
        self.assertIn('NAA: strategy=aggressive behavior=treacherous', report)
        self.assertIn('UE: strategy=passive behavior=loyal', report)

    def test_report_omits_alliance_policies_without_game_state(self):
        stats = GameStats()
        report = stats.report()
        self.assertNotIn('=== Alliance Policies ===', report)

    def test_report_formats_a_new_alliance_forming(self):
        stats = GameStats()
        stats.record_alliance_joined(3, 'NAA', 'UE', 'ALLIANCE_1', new_alliance=True)
        report = stats.report()
        self.assertIn('Turn 3: NAA and UE formed a new alliance (ALLIANCE_1)', report)

    def test_report_formats_joining_an_existing_alliance(self):
        stats = GameStats()
        stats.record_alliance_joined(5, 'NAA', 'AAC', 'pact', new_alliance=False)
        report = stats.report()
        self.assertIn("Turn 5: AAC joined NAA's alliance (pact)", report)

    def test_report_formats_a_withdrawal(self):
        stats = GameStats()
        stats.record_alliance_withdrawal(9, 'NAA', 'pact', {'UE', 'AAC'})
        report = stats.report()
        self.assertIn('Turn 9: NAA withdrew from alliance (pact) -- was allied with AAC, UE', report)

    def test_report_formats_a_withdrawal_with_no_remaining_members(self):
        # A faction's only ally can have already withdrawn earlier,
        # leaving it a solo holdout under a now-empty tag (FactionState.
        # alliance isn't auto-cleared when membership drops to 1) --
        # seen in an actual driven game this session.
        stats = GameStats()
        stats.record_alliance_withdrawal(11, 'NAA', 'ALLIANCE_2', set())
        report = stats.report()
        self.assertIn('Turn 11: NAA withdrew from its now-empty alliance (ALLIANCE_2)', report)

    def test_report_shows_none_when_no_alliance_changes(self):
        stats = GameStats()
        report = stats.report()
        self.assertIn('=== Alliance Changes ===\n(none)', report)


class TestReclaimBonusInCombat(unittest.TestCase):
    def test_reclaim_bonus_is_not_consumed_by_a_single_attempt(self):
        # Confirmed this session: the reclaim bonus (and the territory
        # itself) stays live across repeated attempts -- a betrayer must
        # not get to claim the ground just because the original owner's
        # FIRST counter-attack didn't finish the job. Only
        # process_capture_territory (or the betrayer's own attack
        # failing outright, in _apply_battle_outcome) ever clears it --
        # see TestTrueTerritoryLossBetrayalException.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA', rng=ScriptedRNG([6, 1]))
        self.assertEqual(gs.territories[1].reclaim_bonus_for, 'NAA', 'stays queued for another attempt, not cleared here')

    def test_reclaim_bonus_also_applies_when_defending_not_just_attacking(self):
        # The betrayer (AAC) attacks NAA's freshly marched-in Infantry,
        # sitting in what's still nominally NAA's own territory -- NAA
        # gets the round-1 bonus as DEFENDER this time, confirmed this
        # session ("gets the first round combat bonus on attack and
        # defense in the territory").
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        naa_defender = make_unit('Infantry', 'NAA')  # the reclaim-bonus recipient, now defending
        aac_attacker = make_unit('Infantry', 'AAC')  # the betrayer, attacking
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [naa_defender, aac_attacker]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        # AAC (attacker) rolls 5 -- would cleanly hit (5 <= 5) NAA's
        # unboosted defense-5 Infantry, but misses the round-1-boosted
        # defense-6 defender. NAA's own roll (1) is a safe miss either
        # way. Nobody dies in round 1, so pad the rest to the 3-round cap.
        engine.resolve_combat('AAC', rng=ScriptedRNG([5, 1, 1, 1, 1, 1]))
        self.assertIn(
            naa_defender.unit_id, [u.unit_id for u in gs.territories[1].units],
            'the round-1 defense bonus should have saved the defending original owner',
        )

    def test_reclaim_bonus_left_untouched_for_a_different_attacker(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].reclaim_bonus_for = 'UE'  # some other faction's queued bonus
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA', rng=ScriptedRNG([6, 1]))
        self.assertEqual(gs.territories[1].reclaim_bonus_for, 'UE', "a third party's attack must not consume it")

    def test_reclaim_bonus_actually_changes_combat_math(self):
        # Infantry: D6, defense 5, damage 2, hp 2. Defender's roll of 5
        # cleanly hits (5 <= 5) an UNBOOSTED defense-5 attacker -- but
        # the round-1 bonus (+1 defense) raises it to 6, turning that
        # SAME roll into a clean miss (5 < 6, and 5 isn't the D6 die max
        # either, so not even a bypass hit). Proves the bonus is
        # actually wired into the roll, not just a flag set and cleared
        # around it (already covered above).
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')  # the reclaim-bonus recipient
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].reclaim_bonus_for = 'NAA'
        engine = GameEngine(gs, data)
        # Round 1: attacker rolls 1 (clean miss vs AAC's defense 5
        # regardless); defender rolls 5 -- would cleanly hit a plain
        # defense-5 attacker, but misses the round-1-boosted defense-6
        # attacker. Nobody dies in round 1, so the battle continues to
        # rounds 2-3 (bonus no longer applies); pad with safe misses (1)
        # so it plays out to the 3-round cap without either side dying.
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 5, 1, 1, 1, 1]))
        self.assertIn(
            attacker.unit_id, [u.unit_id for u in gs.territories[1].units],
            'the round-1 defense bonus should have saved the attacker',
        )


class TestAmbushBonusInCombat(unittest.TestCase):
    def test_ambush_bonus_applies_and_is_consumed(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'AAC')  # the prior occupant, queued for the bonus
        defender = make_unit('Cruiser', 'NAA')  # the faction that deployed hostilely
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].ambush_bonus_for = {'AAC'}
        engine = GameEngine(gs, data)
        # Consumption happens the moment resolve_combat determines the
        # bonus applies, before any dice are rolled -- the rolls
        # themselves just need to be enough to play a Cruiser 1v1 (D10,
        # nobody dies to a safe roll of 1) out to the 3-round cap.
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 1, 1, 1, 1, 1]))
        self.assertEqual(gs.territories[1].ambush_bonus_for, set(), "consumed on the named faction's first attempt")

    def test_ambush_bonus_left_untouched_for_a_different_attacker(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].ambush_bonus_for = {'UER'}  # some other faction's queued bonus
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 1, 1, 1, 1, 1]))
        self.assertEqual(gs.territories[1].ambush_bonus_for, {'UER'}, "a third party's attack must not consume it")

    def test_multiple_queued_factions_are_consumed_independently(self):
        # AAC and UER were both caught by the same hostile deploy --
        # only the one that actually attacks first gets consumed; the
        # other stays queued for its own later turn (confirmed this
        # session).
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'AAC')
        defender = make_unit('Cruiser', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UER': FactionMode.HUMAN},
            phase=Phase.COMBAT_RESOLUTION, contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].ambush_bonus_for = {'AAC', 'UER'}
        engine = GameEngine(gs, data)
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 1, 1, 1, 1, 1]))
        self.assertEqual(gs.territories[1].ambush_bonus_for, {'UER'})

    def test_ambush_bonus_actually_changes_combat_math(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'AAC')  # the ambush-bonus recipient
        defender = make_unit('Cruiser', 'NAA')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].ambush_bonus_for = {'AAC'}
        engine = GameEngine(gs, data)
        # Cruiser: D10, defense 7, damage 3, hp 5. Defender's roll of 7
        # cleanly hits (7 <= 7) an unboosted defense-7 attacker, but
        # misses the round-1-boosted defense-8 attacker. Pad the rest so
        # the battle plays out to the 3-round cap without anyone dying.
        engine.resolve_combat('AAC', rng=ScriptedRNG([1, 7, 1, 1, 1, 1]))
        self.assertIn(
            attacker.unit_id, [u.unit_id for u in gs.territories[1].units],
            'the round-1 defense bonus should have saved the attacker',
        )


class TestAmphibiousLandingBonusInCombat(unittest.TestCase):
    def test_bonus_applies_when_every_land_attacker_arrived_amphibiously_this_turn(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        attacker.has_moved_combat = True
        attacker.arrived_amphibiously = True
        defender = make_unit('Infantry', 'AAC')  # the amphibious-landing-bonus recipient
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Attacker's roll of 5 cleanly hits (5 <= 5) an unboosted
        # defense-5 defender, but misses the round-1-boosted defense-6
        # defender. Pad the rest to the 3-round cap.
        engine.resolve_combat('NAA', rng=ScriptedRNG([5, 1, 1, 1, 1, 1]))
        self.assertIn(
            defender.unit_id, [u.unit_id for u in gs.territories[1].units],
            'the round-1 defense bonus should have saved the defender',
        )

    def test_preview_reports_who_has_the_bonus_and_why_without_spending_anything(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        attacker.has_moved_combat = True
        attacker.arrived_amphibiously = True
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        preview = engine.battle_preview('NAA', 1, 'land')
        self.assertEqual(preview['round1_bonus'], {'side': 'defender', 'reason': 'amphibious landing'})

    def test_preview_shows_the_ambush_bonus_but_leaves_the_flag_for_the_battle_to_spend(self):
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        attacker = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        gs.territories[1].ambush_bonus_for.add('NAA')
        engine = GameEngine(gs, data)
        self.assertEqual(engine.battle_preview('NAA', 1, 'sea')['round1_bonus'], {'side': 'attacker', 'reason': 'sea-deploy ambush'})
        self.assertIn('NAA', gs.territories[1].ambush_bonus_for)  # previewing spent nothing
        engine.begin_combat_resolution('NAA')
        engine.resolve_one_battle('NAA', 1, 'sea', ScriptedRNG([1] * 10))
        self.assertNotIn('NAA', gs.territories[1].ambush_bonus_for)  # fighting it did

    def test_preview_has_no_bonus_when_none_applies(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}},
            units_by_territory={1: [make_unit('Infantry', 'NAA'), make_unit('Infantry', 'AAC')]},
        )
        preview = GameEngine(gs, data).battle_preview('NAA', 1, 'land')
        self.assertEqual(preview['round1_bonus'], {'side': None, 'reason': None})

    def test_bonus_does_not_apply_if_any_land_attacker_walked_in_overland(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        amphibious = make_unit('Infantry', 'NAA')
        amphibious.has_moved_combat = True
        amphibious.arrived_amphibiously = True
        overland = make_unit('Mechanized Infantry', 'NAA')
        overland.has_moved_combat = True
        overland.arrived_amphibiously = False
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [amphibious, overland, defender]},
        )
        engine = GameEngine(gs, data)
        # A roll of 5 against an UNBOOSTED defense-5 defender is a clean
        # hit -- if the (wrongly-granted) bonus boosted it to 6, this
        # would miss instead, so a kill here proves no bonus applied.
        events = engine.resolve_combat('NAA', rng=ScriptedRNG([1, 5, 6, 1]))
        self.assertNotIn(
            defender.unit_id, [u.unit_id for u in gs.territories[1].units],
            'one overland attacker should negate the bonus entirely',
        )

    def test_bonus_does_not_apply_to_a_carryover_unit_from_an_earlier_turn(self):
        # A multi-turn stalemate: this land unit is still standing from
        # an earlier turn's amphibious landing, but made no NEW combat
        # move this turn (has_moved_combat False) -- confirmed this
        # session: the bonus is one-shot, not a persistent "hasn't
        # walked since" tracker, so a mere refight never re-grants it.
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Infantry', 'NAA')
        attacker.has_moved_combat = False
        attacker.arrived_amphibiously = True  # stale from an earlier turn
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA', rng=ScriptedRNG([5, 1]))
        self.assertNotIn(
            defender.unit_id, [u.unit_id for u in gs.territories[1].units],
            'a stalemate refought with no fresh combat move must not re-grant the bonus',
        )

    def test_bonus_never_applies_with_zero_land_attackers(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        attacker = make_unit('Fighter', 'NAA')
        attacker.has_moved_combat = True
        defender = make_unit('Infantry', 'AAC')
        gs = make_state(
            data, {1: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [attacker, defender]},
        )
        engine = GameEngine(gs, data)
        # Air units don't roll against ground defense the same way, so
        # just confirm resolve_combat doesn't error and no bonus is
        # otherwise observable -- covered structurally (land_attackers
        # is empty, so "if land_attackers and all(...)" is False, not
        # vacuously True) by the earlier carryover/mixed tests' same
        # code path; this only guards the zero-land-attacker edge itself.
        engine.resolve_combat('NAA', rng=ScriptedRNG([1, 1, 1, 1, 1, 1, 1, 1]))

    def test_bonus_never_applies_to_a_sea_battle(self):
        # A LAND unit CAN end a combat move sitting in open contested
        # water (no land option that turn) -- exactly the case that
        # could slip through if the battle_type == 'land' gate were
        # missing, since it's genuinely a Land-category unit that
        # "arrived amphibiously" by this test's construction. (In a sea
        # battle it is only Transport cargo and never rolls, so a
        # Cruiser attacks alongside it; the cargo is what would wrongly
        # trigger the defender's bonus.) The Cruiser's roll of 7 is a
        # normal defense check against the defender's defense 7 -- and
        # would MISS if the amphibious bonus were wrongly granted
        # (defense 8); D10 die-max is 10, so 7 is no bypass.
        data = FakeData(territories={1: {'type': 'sea'}}, adjacency={})
        cargo = make_unit('Armor', 'NAA')
        cargo.has_moved_combat = True
        cargo.arrived_amphibiously = True
        escort = make_unit('Cruiser', 'NAA')
        defender = make_unit('Cruiser', 'AAC')  # defense 7, hp 5
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION,
            contested={1: {'NAA', 'AAC'}}, units_by_territory={1: [cargo, escort, defender]},
        )
        engine = GameEngine(gs, data)
        engine.resolve_combat('NAA', rng=ScriptedRNG([7, 1, 1, 1, 1, 1, 1, 1]))
        self.assertLess(defender.current_hp, 5, 'the sea battle must not have granted the land-only amphibious bonus')


class TestAdvancePhase(unittest.TestCase):
    def test_steps_through_the_full_sequence_and_then_stops(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        engine = GameEngine(gs, data)
        expected = [
            Phase.COMBAT_MOVE, Phase.COMBAT_RESOLUTION, Phase.NONCOMBAT_MOVE,
            Phase.CAPTURE, Phase.DEPLOY_INCOME, Phase.DIPLOMACY,
        ]
        for phase in expected:
            engine.advance_phase()
            self.assertEqual(gs.phase, phase)
        engine.advance_phase()  # one more call past the end -- stays put
        self.assertEqual(gs.phase, Phase.DIPLOMACY)


class TestFirstTurnGameStartSettings(unittest.TestCase):
    """game_start_settings: GameState.allow_combat_moves_first_turn /
    allow_noncombat_moves_first_turn, enforced by advance_phase() only
    for the active faction's own first turn (FactionState.turns_taken
    == 0) -- never a later turn, and never for a faction that ISN'T
    active_faction."""
    def test_combat_move_is_skipped_on_a_first_turn_when_disallowed(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        gs.active_faction = 'NAA'
        gs.allow_combat_moves_first_turn = False
        engine = GameEngine(gs, data)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.COMBAT_RESOLUTION, 'Combat Move skipped entirely')

    def test_noncombat_move_is_skipped_on_a_first_turn_when_disallowed(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION)
        gs.active_faction = 'NAA'
        gs.allow_noncombat_moves_first_turn = False
        engine = GameEngine(gs, data)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.CAPTURE, 'Non-Combat Move skipped entirely')

    def test_both_skipped_when_both_disallowed(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        gs.active_faction = 'NAA'
        gs.allow_combat_moves_first_turn = False
        gs.allow_noncombat_moves_first_turn = False
        engine = GameEngine(gs, data)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.COMBAT_RESOLUTION)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.CAPTURE, 'both move phases skipped, Combat Resolution never skipped')

    def test_not_skipped_by_default_settings(self):
        # allow_combat_moves_first_turn defaults False but
        # allow_noncombat_moves_first_turn defaults True -- Combat Move
        # is skipped, Non-Combat Move is not.
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.COMBAT_RESOLUTION)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.NONCOMBAT_MOVE, 'allowed by default')

    def test_not_skipped_once_the_faction_has_had_a_prior_turn(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.PURCHASE)
        gs.active_faction = 'NAA'
        gs.allow_combat_moves_first_turn = False
        gs.factions['NAA'].turns_taken = 1  # already had a turn -- this is its second
        engine = GameEngine(gs, data)
        engine.advance_phase()
        self.assertEqual(gs.phase, Phase.COMBAT_MOVE, 'the setting only ever applies to a faction\'s first turn')

    def test_advance_turn_increments_turns_taken(self):
        data = FakeData(territories={1: {'type': 'land', 'value': 0, 'strategic_center': True}}, adjacency={})
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        self.assertEqual(gs.factions['NAA'].turns_taken, 0)
        engine.advance_turn()
        self.assertEqual(gs.factions['NAA'].turns_taken, 1)


class TestAdvanceTurn(unittest.TestCase):
    def test_resets_the_finishing_factions_move_flags(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        mover = make_unit('Infantry', 'NAA')
        mover.has_moved_combat = True
        mover.has_moved_noncombat = True
        gs = make_state(
            data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
            units_by_territory={1: [mover]},
        )
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertFalse(mover.has_moved_combat)
        self.assertFalse(mover.has_moved_noncombat)

    def test_clears_phase_confirmation_guards_so_the_next_turn_works(self):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        gs = make_state(data, {1: 'NAA'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        # Simulate NAA having already confirmed every phase this turn.
        engine._purchases_confirmed.add('NAA')
        engine._combat_moves_confirmed.add('NAA')
        engine._combat_resolved.add('NAA')
        engine._noncombat_moves_confirmed.add('NAA')
        engine._return_to_base_processed.add('NAA')
        engine.advance_turn()
        self.assertNotIn('NAA', engine._purchases_confirmed)
        self.assertNotIn('NAA', engine._combat_moves_confirmed)
        self.assertNotIn('NAA', engine._combat_resolved)
        self.assertNotIn('NAA', engine._noncombat_moves_confirmed)
        self.assertNotIn('NAA', engine._return_to_base_processed)
        # And NAA can genuinely submit purchases again, once it's active again.
        gs.active_faction = 'NAA'
        engine.submit_purchases('NAA', [])  # should not raise

    def test_cycles_to_the_next_active_faction_and_wraps_around(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertEqual(gs.active_faction, 'AAC')
        gs.phase = Phase.DIPLOMACY
        engine.advance_turn()
        self.assertEqual(gs.active_faction, 'UE')
        gs.phase = Phase.DIPLOMACY
        engine.advance_turn()
        self.assertEqual(gs.active_faction, 'NAA', 'wraps back around to the first')

    def test_skips_a_faction_eliminated_since_its_turn_began(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(
            data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN, 'UE': FactionMode.HUMAN}, phase=Phase.DIPLOMACY,
        )
        gs.active_faction = 'NAA'
        gs.factions['AAC'].eliminated = True  # eliminated during NAA's own turn
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertEqual(gs.active_faction, 'UE', 'AAC is skipped -- no longer an active faction')

    def test_increments_global_turn(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY, global_turn=5)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertEqual(gs.global_turn, 6)

    def test_resets_phase_to_purchase(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertEqual(gs.phase, Phase.PURCHASE)

    def test_sets_game_over_when_no_active_factions_remain(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        gs.factions['NAA'].eliminated = True  # the only active faction, gone
        engine = GameEngine(gs, data)
        engine.advance_turn()
        self.assertTrue(gs.game_over)
        self.assertIsNone(gs.active_faction)

    def test_raises_if_the_game_is_already_over(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.DIPLOMACY)
        gs.game_over = True
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.advance_turn()

    def test_raises_if_not_at_the_alliances_phase(self):
        data = FakeData(territories={}, adjacency={})
        gs = make_state(data, {}, {'NAA': FactionMode.HUMAN}, phase=Phase.CAPTURE)
        engine = GameEngine(gs, data)
        with self.assertRaises(ValueError):
            engine.advance_turn()


class TestFullTurnLoopIntegration(unittest.TestCase):
    """Drives several complete turns across multiple factions through
    the real public API -- no bot decision-making (Step 5), just empty/
    trivial orders -- to prove the orchestration (advance_phase,
    advance_turn, and every phase method chained together in sequence)
    actually holds up over multiple turns, which none of the more
    targeted tests above exercise all at once."""

    def _run_one_turn(self, engine, gs, faction):
        self.assertEqual(gs.active_faction, faction)
        self.assertEqual(gs.phase, Phase.PURCHASE)
        engine.submit_purchases(faction, [])
        engine.confirm_purchases(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.COMBAT_MOVE)
        engine.submit_combat_moves(faction, [])
        engine.confirm_combat_moves(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.COMBAT_RESOLUTION)
        engine.resolve_combat(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.NONCOMBAT_MOVE)
        engine.process_return_to_base(faction)
        engine.submit_noncombat_moves(faction, [])
        engine.confirm_noncombat_moves(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.CAPTURE)
        engine.process_capture_territory(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.DEPLOY_INCOME)
        engine.deploy_and_collect_income(faction)
        engine.advance_phase()

        self.assertEqual(gs.phase, Phase.DIPLOMACY)
        engine.process_game_end_check(faction)
        engine.advance_turn()

    def test_four_full_turns_across_two_factions(self):
        # Each faction needs >=2 Strategic Centers to survive
        # process_elimination_check (part of the standard turn loop) --
        # 1 SC would eliminate them ("<=1" is the elimination rule).
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 3, 'strategic_center': True},
                2: {'type': 'land', 'value': 2, 'strategic_center': True},
                3: {'type': 'land', 'value': 0, 'strategic_center': True},
                4: {'type': 'land', 'value': 0, 'strategic_center': True},
            },
            adjacency={},
        )
        gs = make_state(
            data, {1: 'NAA', 2: 'AAC', 3: 'NAA', 4: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.PURCHASE, treasury={'NAA': 0, 'AAC': 0},
        )
        gs.active_faction = 'NAA'
        # This test exercises the FULL 7-phase sequence every turn --
        # game_start_settings' first-turn skip (default: no combat moves
        # on a faction's own first turn) isn't what it's testing.
        gs.allow_combat_moves_first_turn = True
        engine = GameEngine(gs, data)

        expected_order = ['NAA', 'AAC', 'NAA', 'AAC']
        for turn, faction in enumerate(expected_order):
            self.assertEqual(gs.global_turn, turn)
            self._run_one_turn(engine, gs, faction)

        self.assertEqual(gs.global_turn, 4)
        self.assertEqual(gs.active_faction, 'NAA')
        # Income collected twice each (turns 0&2 for NAA, 1&3 for AAC):
        # NAA's two territories are worth 3+2(SC)=5 and 0+2(SC)=2 -> 7/turn.
        # AAC's are 2+2(SC)=4 and 0+2(SC)=2 -> 6/turn.
        self.assertEqual(gs.factions['NAA'].treasury_mpc, 14)
        self.assertEqual(gs.factions['AAC'].treasury_mpc, 12)
        self.assertFalse(gs.game_over)

    def test_a_units_move_flags_reset_by_the_time_its_faction_acts_again(self):
        # NAA needs >=2 SCs and AAC needs >=1 (2 to be safe) to both
        # survive process_elimination_check, part of the standard loop.
        data = FakeData(
            territories={
                1: {'type': 'land', 'value': 0, 'strategic_center': True}, 2: {'type': 'land', 'value': 0, 'strategic_center': True},
                3: {'type': 'land', 'value': 0, 'strategic_center': True}, 4: {'type': 'land', 'value': 0, 'strategic_center': True},
            },
            adjacency={1: [2], 2: [1]},
        )
        mover = make_unit('Infantry', 'NAA')
        gs = make_state(
            data, {1: 'NAA', 2: 'NAA', 3: 'AAC', 4: 'AAC'}, {'NAA': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN},
            phase=Phase.PURCHASE, units_by_territory={1: [mover]},
        )
        gs.active_faction = 'NAA'
        # Turn 0 below submits a real (if empty) combat move and a real
        # non-combat move for NAA -- needs both first-turn phases enabled.
        gs.allow_combat_moves_first_turn = True
        engine = GameEngine(gs, data)

        # Turn 0 (NAA): move the unit via non-combat move.
        engine.submit_purchases('NAA', [])
        engine.confirm_purchases('NAA')
        engine.advance_phase()
        engine.submit_combat_moves('NAA', [])
        engine.confirm_combat_moves('NAA')
        engine.advance_phase()
        engine.resolve_combat('NAA')
        engine.advance_phase()
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 2)])
        engine.confirm_noncombat_moves('NAA')
        self.assertTrue(mover.has_moved_noncombat)
        engine.advance_phase()
        engine.process_capture_territory('NAA')
        engine.advance_phase()
        engine.deploy_and_collect_income('NAA')
        engine.advance_phase()
        engine.process_game_end_check('NAA')
        engine.advance_turn()

        # Turn 1 (AAC): nothing to do with NAA's unit.
        self._run_one_turn(engine, gs, 'AAC')

        # Turn 2 (NAA again): the SAME unit should be able to move once more.
        self.assertFalse(mover.has_moved_noncombat, 'reset by advance_turn once NAA\'s turn closed')
        engine.submit_purchases('NAA', [])
        engine.confirm_purchases('NAA')
        engine.advance_phase()
        engine.submit_combat_moves('NAA', [])
        engine.confirm_combat_moves('NAA')
        engine.advance_phase()
        engine.resolve_combat('NAA')
        engine.advance_phase()
        engine.process_return_to_base('NAA')
        engine.submit_noncombat_moves('NAA', [NonCombatMoveOrder(mover.unit_id, 1)])  # should not raise
        engine.confirm_noncombat_moves('NAA')
        self.assertIn(mover, gs.territories[1].units)


if __name__ == '__main__':
    unittest.main()


class TestWithdrawingFromAPairDissolvesIt(unittest.TestCase):
    def _setup(self, members):
        data = FakeData(territories={1: {'type': 'land'}}, adjacency={})
        modes = {'NAA': FactionMode.HUMAN, 'UE': FactionMode.HUMAN, 'GPC': FactionMode.HUMAN, 'AAC': FactionMode.HUMAN}
        gs = make_state(data, {1: 'NAA'}, modes, phase=Phase.DIPLOMACY)
        gs.active_faction = 'NAA'
        for code in members:
            gs.factions[code].alliance = 'ALLIANCE_1'
        return GameEngine(gs, data), gs

    def test_leaving_a_two_member_alliance_frees_the_other_member_too(self):
        engine, gs = self._setup(['NAA', 'UE'])
        engine.withdraw_from_alliance('NAA')
        self.assertIsNone(gs.factions['NAA'].alliance)
        self.assertIsNone(gs.factions['UE'].alliance)  # no one-member alliance left behind
        self.assertIn('UE', gs.factions['NAA'].former_allies)

    def test_leaving_a_larger_alliance_leaves_the_rest_allied(self):
        engine, gs = self._setup(['NAA', 'UE', 'GPC'])
        engine.withdraw_from_alliance('NAA')
        self.assertEqual(gs.factions['UE'].alliance, 'ALLIANCE_1')
        self.assertEqual(gs.factions['GPC'].alliance, 'ALLIANCE_1')
