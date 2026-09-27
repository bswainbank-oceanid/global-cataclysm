"""The deployment rules (engine/deployment.py): Armor is never built or deployed on an island -- in
any starting setup, by the setup generators, or by a bot's purchases -- but a human may still buy it."""
import copy
import os
import random
import shutil
import tempfile
import unittest

from engine import data, deployment
from engine import scenario_generator as gen
from engine.bots.driver import play_to_completion
from engine.engine import GameEngine, PurchaseOrder
from engine.game_config import GameConfig
from engine.repository import DEFAULT_MODULES_DIR, ModuleRepository, default_repository
from engine.setup import build_game_state
from engine.state import FactionMode
from server.lobby import build_session

ISLANDS = {'Ireland', 'Hawaii', 'Cuba', 'Philippines', 'Indonesia', 'New Guinea', 'Madagascar', 'New Zealand',
           'Polynesia', 'Scotland', 'England', 'Northern Japan', 'Southern Japan', 'Western Australia',
           'Eastern Australia'}


def island_ids():
    return deployment.islands(data)


class TestIslands(unittest.TestCase):
    def test_the_gc72_islands(self):
        terrs = data.territories()
        self.assertEqual({terrs[t]['name'] for t in island_ids()}, ISLANDS)

    def test_armor_becomes_mechanized_infantry_on_an_island_only(self):
        island = next(iter(island_ids()))
        mainland = next(t for t, i in data.territories().items() if i['type'] == 'land' and t not in island_ids())
        self.assertEqual(deployment.substitute(data, 'Armor', island), 'Mechanized Infantry')
        self.assertEqual(deployment.substitute(data, 'Armor', mainland), 'Armor')
        self.assertEqual(deployment.substitute(data, 'Infantry', island), 'Infantry')


class TestStartingSetups(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = ModuleRepository(os.path.join(self.dir, 'modules'))
        shutil.copytree(DEFAULT_MODULES_DIR, self.repo.root)

    def tearDown(self):
        data.use_scenario()
        shutil.rmtree(self.dir)

    def test_armor_in_a_setup_is_placed_as_mechanized_infantry_on_an_island(self):
        england = next(t for t, i in data.territories().items() if i['name'] == 'England')
        setup = copy.deepcopy(self.repo.get('InitialSetup', 'GC72_StandardSetup'))
        loc = next(l for l in setup['locations'] if l['location_id'] == england)
        faction = loc['units'][0]['faction_id']
        loc['units'].append({'id': f'{faction}-900', 'unit_type_id': 'Armor', 'faction_id': faction})
        self.repo.save(setup)
        self.repo.clear_cache()
        data.use_scenario(repository=self.repo)
        gs = build_game_state({c: FactionMode.BOT for c in data.factions()}, rng=random.Random(1))
        types = [u.unit_type for u in gs.territories[england].units]
        self.assertNotIn('Armor', types)
        self.assertIn('Mechanized Infantry', types)

    def test_the_new_scenario_generator_never_puts_armor_on_an_island(self):
        b, g = GameConfig(), default_repository().get('ScenarioGenerator', 'GC72_Generator')
        players = list(b.factions())
        islands = deployment.islands(b)
        for seed in range(6):
            seats, _ = gen.resolve_seats(b, g['seat_defaults'], g['neutral_defaults'], players, {}, None)
            repo, sid, _ = gen.generate(b, seats, gen.resolve_options(b, g['defaults'], {}), random.Random(seed))
            setup, _ = GameConfig(sid, repo).initial_setup('standard')
            for loc in setup['locations']:
                if loc['location_id'] in islands:
                    self.assertNotIn('Armor', [u['unit_type_id'] for u in loc['units']], f'seed {seed}')


class TestPurchases(unittest.TestCase):
    def test_bots_never_buy_armor_for_an_island(self):
        islands = island_ids()
        for ai in ('random', 'strategy'):
            seat = {'mode': 'BOT', 'faction': 'random', 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': ai}
            session, _ = build_session({'seats': [dict(seat) for _ in range(6)], 'seed': 3})
            play_to_completion(session.engine, session.bots, max_turns=18)
            bought = [(o['unit_type'], o['deploy_at']) for e in session.turn_log.events if e['kind'] == 'purchase'
                      for o in e['orders']]
            self.assertTrue(bought)
            self.assertFalse([b for b in bought if b[0] == 'Armor' and b[1] in islands], ai)

    def test_a_human_may_still_buy_armor_for_an_island(self):
        gs = build_game_state({c: FactionMode.HUMAN if c == 'NAA' else FactionMode.BOT for c in data.factions()},
                              randomize_play_order=False, rng=random.Random(1))
        engine = GameEngine(gs, data)
        england = next(t for t, i in data.territories().items() if i['name'] == 'England')
        self.assertEqual(gs.territories[england].owner, 'NAA')
        engine.submit_purchases('NAA', [PurchaseOrder('Armor', 1, england)])


if __name__ == '__main__':
    unittest.main()
