"""The New Scenario generator (engine/scenario_generator.py) deals territory, Strategic Centers and units
by its rules, from the seed alone -- and the lobby plays the game it deals."""
import json
import random
import unittest

from engine import scenario_generator as gen
from engine.bots.driver import play_to_completion
from engine.game_config import GameConfig
from engine.module_validation import validate_repository
from engine.repository import default_repository
from server.lobby import build_session, check_settings

PLAYERS_3 = ['NAA', 'GPC', 'PAF']
PLAYERS_6 = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']


def base():
    return GameConfig()


def generator():
    return default_repository().get('ScenarioGenerator', 'GC72_Generator')


def deal(players, seed=1, seat=None, neutral=None, per_faction=None, **overrides):
    """A deal for `players`: `seat` settings for every player, `per_faction` {faction: settings} on top,
    `neutral` the Neutral row, `overrides` the scenario-wide options."""
    b, g = base(), generator()
    per = {f: dict(seat or {}, **((per_faction or {}).get(f) or {})) for f in players}
    seats, notes = gen.resolve_seats(b, g['seat_defaults'], g['neutral_defaults'], players, per, neutral)
    options = gen.resolve_options(b, g['defaults'], overrides)
    repo, sid, report = gen.generate(b, seats, options, random.Random(seed), notes=notes)
    return b, options, GameConfig(sid, repo), repo, report


def value_held(b, owner, faction):
    return sum(b.territories()[t]['value'] for t, o in owner.items() if o == faction)


class TestTerritory(unittest.TestCase):
    def test_each_player_gets_its_territory_value(self):
        for players in (PLAYERS_6, PLAYERS_3):
            b, options, _, _, report = deal(players, seat={'territory_value': 90 // len(players)})
            limit = 90 // len(players)
            for f in players:
                self.assertLessEqual(value_held(b, report['owner'], f), limit)
                self.assertGreaterEqual(value_held(b, report['owner'], f), limit - 1)
        # each seat its own
        b, _, _, _, report = deal(PLAYERS_3, per_faction={'NAA': {'territory_value': 40}, 'GPC': {'territory_value': 10}})
        self.assertEqual(value_held(b, report['owner'], 'NAA'), 40)
        self.assertEqual(value_held(b, report['owner'], 'GPC'), 10)

    def test_the_default_split_and_the_scale_down(self):
        b, g = base(), generator()
        seats, notes = gen.resolve_seats(b, g['seat_defaults'], g['neutral_defaults'], PLAYERS_3, {}, None)
        self.assertEqual([v['territory_value'] for v in seats['players'].values()], [50, 50, 50])
        self.assertEqual(seats['neutral']['territory_value'], 0)
        self.assertEqual(notes, [])
        seats, notes = gen.resolve_seats(b, g['seat_defaults'], g['neutral_defaults'], ['NAA', 'GPC'],
                                         {'NAA': {'territory_value': 150}, 'GPC': {'territory_value': 100}},
                                         {'territory_value': 50})
        self.assertLessEqual(sum(v['territory_value'] for v in seats['players'].values())
                             + seats['neutral']['territory_value'], 150)
        self.assertEqual(seats['players']['NAA']['territory_value'], 75)
        self.assertTrue(notes)

    def test_the_neutral_pool_and_noncombatant_land(self):
        b, _, _, _, report = deal(PLAYERS_3, seat={'territory_value': 30}, neutral={'territory_value': 30})
        self.assertLessEqual(value_held(b, report['owner'], 'NEU'), 30)
        self.assertGreater(sum(1 for o in report['owner'].values() if o == 'NCB'), 0)
        # by default the Neutral pool takes everything the players leave
        _, _, _, _, report = deal(PLAYERS_3, seat={'territory_value': 30})
        self.assertNotIn('NCB', report['owner'].values())
        self.assertEqual(len(report['owner']), sum(1 for t in b.territories().values() if t['type'] == 'land'))

    def test_the_faction_weight_favours_a_factions_own_base_territory(self):
        b = base()
        home = {f: {t for t, info in b.territories().items() if info.get('faction') == f} for f in PLAYERS_6}
        def own_share(weight):
            _, _, _, _, report = deal(PLAYERS_6, seed=3, faction_weight=weight)
            return sum(1 for t, o in report['owner'].items() if o in home and t in home[o]) / len(report['owner'])
        self.assertGreater(own_share(50), own_share(0))


class TestStrategicCenters(unittest.TestCase):
    def test_counts_and_distances(self):
        b, options, cfg, _, report = deal(PLAYERS_3, seed=2, seat={'territory_value': 30},
                                          per_faction={'PAF': {'scs': 1}},
                                          neutral={'territory_value': 40, 'scs': 2})
        scs, owner = report['strategic_centers'], report['owner']
        dist = gen._distances(b.adjacency())
        for f in PLAYERS_3:
            self.assertEqual(sum(1 for t in scs if owner[t] == f), 1 if f == 'PAF' else 3)
        self.assertEqual(sum(1 for t in scs if owner[t] == 'NEU'), 2)
        if not report['notes']:  # nothing relaxed: every pair at least the minimum apart
            for a in scs:
                for c in scs:
                    if a != c:
                        self.assertGreaterEqual(dist[a][c], options['sc_min_distance'])
        self.assertEqual(sorted(cfg.sc_assignment['locations']), sorted(scs))
        self.assertEqual(cfg.sc_bonus(), options['sc_bonus'])

    def test_impossible_distances_are_relaxed_and_reported(self):
        _, _, _, _, report = deal(PLAYERS_6, sc_min_distance=12, sc_final_min_distance=12)
        self.assertEqual(len(report['strategic_centers']), 18)
        self.assertTrue(any('relaxed' in n for n in report['notes']))


class TestUnits(unittest.TestCase):
    def test_budget_carryover_and_the_setup_rules(self):
        b, options, cfg, _, report = deal(PLAYERS_6, seed=4, per_faction={'NAA': {'initial_mpc': 160, 'promotions': 9}})
        setup, promotions = cfg.initial_setup('standard')
        seats = cfg.scenario['generated']['seats']['players']
        units, terrs = b.units(), b.territories()
        scs = set(report['strategic_centers'])
        for f in PLAYERS_6:
            spent, per_territory = 0, {}
            for loc in setup['locations']:
                for u in loc['units']:
                    if u['faction_id'] != f:
                        continue
                    bought = u.get('purchased_at', loc['location_id'])
                    spent += units[u['unit_type_id']]['sc_cost' if bought in scs else 'cost']
                    per_territory[bought] = per_territory.get(bought, 0) + 1
            self.assertLessEqual(spent, seats[f]['units_mpc'])
            self.assertEqual(spent + setup['carryover_mpc'][f], seats[f]['initial_mpc'])
            for t, n in per_territory.items():
                self.assertLessEqual(n, terrs[t]['value'] + 3 + (options['sc_bonus'] if t in scs else 0))
            held = [t for t, o in report['owner'].items() if o == f]
            self.assertTrue(all(t in per_territory for t in held))  # an Infantry in every territory
        self.assertEqual(len(promotions['units']), 3 * 5 + 9)  # more promotions than types: second units of the top ones

    def test_neutral_units_are_land_and_air_and_promoted_as_asked(self):
        b, _, cfg, _, _ = deal(PLAYERS_3, seat={'territory_value': 30}, neutral={'territory_value': 40})
        setup, promotions = cfg.initial_setup('neutral')
        types = {u['unit_type_id'] for loc in setup['locations'] for u in loc['units']}
        self.assertTrue(types)
        self.assertTrue(all(b.units()[t]['category'] in ('Land', 'Air') for t in types))
        self.assertEqual(promotions['units'], [])  # the default: none
        _, _, cfg, _, _ = deal(PLAYERS_3, seat={'territory_value': 30}, neutral={'territory_value': 40, 'promotions': 2})
        self.assertEqual(len(cfg.initial_setup('neutral')[1]['units']), 2)

    def test_the_generated_modules_validate(self):
        for players in (PLAYERS_6, PLAYERS_3):
            _, _, _, repo, _ = deal(players, seat={'territory_value': 90 // len(players)},
                                    neutral={'territory_value': 30, 'scs': 2})
            self.assertEqual([p for p in validate_repository(repo) if 'GEN_' in p], [])


class TestTheSameSeedDealsTheSameGame(unittest.TestCase):
    def test_same_seed_same_deal(self):
        docs = []
        for _ in range(2):
            _, _, cfg, repo, _ = deal(PLAYERS_3, seed=7, seat={'territory_value': 30}, neutral={'territory_value': 30})
            docs.append(json.dumps([repo.get(t, i) for t, i in repo._docs], sort_keys=True))
        self.assertEqual(docs[0], docs[1])
        _, _, _, repo, _ = deal(PLAYERS_3, seed=8, seat={'territory_value': 30}, neutral={'territory_value': 30})
        self.assertNotEqual(docs[0], json.dumps([repo.get(t, i) for t, i in repo._docs], sort_keys=True))


def bot(faction='random'):
    return {'mode': 'BOT', 'faction': faction, 'alliance': 0, 'strategy': 'random', 'behavior': 'random', 'ai': 'random'}


NOT_PLAYING = {'mode': 'NOT_PLAYING', 'faction': 'random'}


class TestLobby(unittest.TestCase):
    def settings(self, seats, neutral=None, **options):
        return {'scenario': {'kind': 'new', 'options': options, 'neutral': neutral or {}}, 'seats': seats, 'seed': 21}

    def test_a_new_scenario_game_leaves_out_the_factions_not_playing(self):
        session, seats = build_session(self.settings([bot('NAA'), bot('UE'), bot(), NOT_PLAYING, NOT_PLAYING, NOT_PLAYING],
                                                     neutral={'territory_value': 30}))
        gs = session.engine.game_state
        playing = [s['faction'] for s in seats if s['mode'] == 'BOT']
        self.assertEqual(set(gs.factions), set(playing) | {'NEU', 'NCB'})
        self.assertEqual(set(gs.active_factions()), set(playing))
        play_to_completion(session.engine, session.bots, max_turns=20)

    def test_a_seeded_new_scenario_replays_exactly(self):
        def run():
            session, _ = build_session(self.settings([bot(), bot(), bot(), NOT_PLAYING, NOT_PLAYING, NOT_PLAYING]))
            play_to_completion(session.engine, session.bots, max_turns=12)
            return json.dumps(session.turn_log.events, sort_keys=True)
        self.assertEqual(run(), run())

    def test_the_seats_and_options_are_checked(self):
        np = NOT_PLAYING
        self.assertTrue(check_settings(self.settings([bot(), bot(), {'mode': 'NEUTRAL', 'faction': 'random'}, np, np, np])))
        self.assertTrue(check_settings(self.settings([bot(), bot(), np, np, np, np], sc_bonus=99)))
        self.assertTrue(check_settings(self.settings([bot(), bot(), np, np, np, np], teleporters=True)))
        self.assertEqual(check_settings(self.settings([bot(), bot(), np, np, np, np])), [])
        # a seat's own settings are checked too
        self.assertTrue(check_settings(self.settings([dict(bot(), initial_mpc=50, units_mpc=80), bot(), np, np, np, np])))
        self.assertTrue(check_settings(self.settings([bot(), bot(), np, np, np, np], neutral={'scs': -1})))
        # a fixed game has no NOT_PLAYING seats
        fixed = {'seats': [bot(), bot(), np, np, np, np]}
        self.assertTrue(check_settings(fixed))


if __name__ == '__main__':
    unittest.main()
