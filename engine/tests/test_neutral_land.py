"""Land the faction assignment leaves unassigned belongs to the built-in Neutral faction: it plays like a
Neutral seat (units defend, no turns, capturable, never a Strategic Center), and only exists when it owns
something."""
import copy
import os
import random
import shutil
import tempfile
import unittest

from engine import data
from engine.bots.driver import play_to_completion
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.module_validation import validate_repository
from engine.repository import DEFAULT_MODULES_DIR, ModuleRepository
from engine.setup import build_game_state
from engine.state import FactionMode

ALASKA = 6          # a UER territory in GC72
NORWAY = 7          # a UER Strategic Center


class TestNeutralLand(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        root = os.path.join(self.dir, 'modules')
        shutil.copytree(DEFAULT_MODULES_DIR, root)
        self.repo = ModuleRepository(root)
        fa = copy.deepcopy(self.repo.get('FactionAssignment', 'GC72_FactionAssignment'))
        for row in fa['locations']:
            if row['location_id'] in (ALASKA, NORWAY):
                row['faction_id'] = None
        self.repo.save(fa)
        # the Neutral faction's own starting unit, from the scenario's neutral setup
        setup = copy.deepcopy(self.repo.get('InitialSetup', 'GC72_NeutralSetup'))
        norway = next((loc for loc in setup['locations'] if loc['location_id'] == NORWAY), None)
        if norway is None:
            norway = {'location_id': NORWAY, 'units': []}
            setup['locations'].append(norway)
        norway['units'].append({'id': 'NEU-001', 'unit_type_id': 'Infantry', 'faction_id': 'NEU'})
        self.repo.save(setup)
        self.repo.clear_cache()
        data.use_scenario(repository=self.repo)

    def tearDown(self):
        data.use_scenario()
        shutil.rmtree(self.dir)

    def modes(self):
        return {c: FactionMode.BOT for c in data.factions()}

    def test_the_modules_still_validate(self):
        self.assertEqual(validate_repository(self.repo), [])

    def test_unassigned_land_is_the_neutral_factions(self):
        gs = build_game_state(self.modes(), rng=random.Random(1))
        self.assertEqual(data.unassigned_land(), [ALASKA, NORWAY])
        self.assertEqual(gs.territories[ALASKA].owner, 'NEU')
        self.assertEqual(gs.factions['NEU'].mode, FactionMode.NEUTRAL)
        self.assertNotIn('NEU', gs.active_factions())
        self.assertEqual(list(gs.factions)[-1], 'NEU')  # never in the play order
        self.assertFalse(gs.is_strategic_center(NORWAY, data.territories()[NORWAY]))
        self.assertEqual([u.unit_type for u in gs.territories[NORWAY].units if u.owner == 'NEU'], ['Infantry'])

    def test_a_game_with_neutral_land_plays(self):
        gs = build_game_state(self.modes(), rng=random.Random(2), allow_combat_moves_first_turn=True)
        engine = GameEngine(gs, data, combat_rng=random.Random(3))
        bots = {c: RandomBot(engine, c, rng=random.Random(i)) for i, c in enumerate(gs.active_factions())}
        play_to_completion(engine, bots, max_turns=30)
        self.assertEqual(gs.factions['NEU'].turns_taken, 0)


class TestNoNeutralFactionWithoutNeutralLand(unittest.TestCase):
    def test_gc72_has_no_unassigned_land(self):
        data.use_scenario()
        self.assertEqual(data.unassigned_land(), [])
        gs = build_game_state({c: FactionMode.BOT for c in data.factions()}, rng=random.Random(1))
        self.assertNotIn('NEU', gs.factions)


if __name__ == '__main__':
    unittest.main()
