"""The multi-player server's scenarios, lobbies and chat (server/hub.py, stage 2d)."""
import shutil
import tempfile
import unittest
from unittest import mock

from engine.repository import ModuleRepository
from server import accounts, games, setups
from server.hub import Hub
from server.store import Store
from server.tests.test_hub import ONE_HUMAN, TWO_HUMANS, settings
from server.tests.test_persist import bot, seat
from server.tests.test_setups import new_settings

FIXED = {'kind': 'fixed', 'id': 'fixed'}


class LobbyTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(accounts, 'ITERATIONS', 1000)
        patch.start()
        self.addCleanup(patch.stop)
        self.dir = tempfile.mkdtemp()
        shutil.copytree(ModuleRepository().root, f'{self.dir}/modules', dirs_exist_ok=True)
        shutil.rmtree(f'{self.dir}/modules/scenario_setup', ignore_errors=True)
        self.repo = ModuleRepository(f'{self.dir}/modules')
        self.duel = setups.save({'id': None, 'name': 'Duel', 'settings': new_settings()}, self.repo)
        self.store = Store(':memory:')
        self.hub = Hub(self.store, self.repo)
        self.ann = self.user('ann', 'Ann')
        self.bob = self.user('bob', 'Bob')
        self.cat = self.user('cat', 'Cat')

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def user(self, key, name):
        self.hub.connect(key)
        [(_, msg)] = self.hub.handle(key, {'type': 'register', 'player_name': name, 'email': f'{key}@x.com', 'password': 'pw'})
        return msg['user']['id']

    def send(self, key, msg):
        return self.hub.handle(key, msg)

    def only(self, key, msg):
        """The one message `msg` sends back to its sender (and nothing to anyone else)."""
        [(keys, reply)] = self.send(key, msg)
        self.assertEqual(keys, [key])
        return reply

    def got(self, deliveries, key):
        return [m for keys, m in deliveries if key in keys]

    def lobby(self, host='ann', s=TWO_HUMANS):
        reply = self.send(host, {'type': 'create_game', 'scenario': FIXED, 'settings': s})
        lobby = self.got(reply, host)[0]
        self.assertEqual(lobby['type'], 'game_lobby')
        return lobby['game']


class TestScenarios(LobbyTest):
    def test_the_listing_has_built_ins_shared_and_own_with_the_generator(self):
        reply = self.only('ann', {'type': 'scenarios'})
        self.assertEqual([r['kind'] for r in reply['scenarios']], ['fixed', 'new', 'shared'])
        self.assertIn('options', reply['new_scenario'])
        self.assertFalse(reply['admin'])

    def test_save_and_reset_personal_settings(self):
        reply = self.only('ann', {'type': 'save_settings', 'scenario_id': self.duel,
                                  'settings': new_settings(randomize_order=False)})
        row = next(r for r in reply['scenarios'] if r['id'] == self.duel)
        self.assertTrue(row['personal'])
        self.assertEqual(reply['selected'], self.duel)
        reply = self.only('ann', {'type': 'reset_settings', 'scenario_id': self.duel})
        self.assertFalse(next(r for r in reply['scenarios'] if r['id'] == self.duel)['personal'])

    def test_own_scenarios_save_and_delete(self):
        reply = self.only('ann', {'type': 'save_scenario', 'scenario': {'name': 'Mine', 'settings': new_settings()}})
        sid = reply['selected']
        self.assertEqual(next(r for r in reply['scenarios'] if r['id'] == sid)['kind'], 'own')
        self.assertEqual(self.only('bob', {'type': 'delete_scenario', 'id': sid})['problems'],
                         ['you can only delete your own scenarios'])
        reply = self.only('ann', {'type': 'delete_scenario', 'id': sid})
        self.assertNotIn(sid, [r['id'] for r in reply['scenarios']])

    def test_shared_scenarios_need_an_admin(self):
        change = {'type': 'save_shared', 'scenario': {'id': self.duel, 'name': 'Duel', 'description': 'new',
                                                      'settings': new_settings()}}
        self.assertEqual(self.only('ann', change)['problems'], ['only an admin can change the shared scenarios'])
        accounts.set_admin(self.store, self.ann)
        reply = self.only('ann', change)
        self.assertTrue(reply['admin'])
        self.assertEqual(next(r for r in reply['scenarios'] if r['id'] == self.duel)['description'], 'new')
        reply = self.only('ann', {'type': 'delete_shared', 'id': self.duel})
        self.assertNotIn(self.duel, [r['id'] for r in reply['scenarios']])


class TestCreatingGames(LobbyTest):
    def test_one_human_starts_at_once_and_puts_the_host_in_it(self):
        out = self.send('ann', {'type': 'create_game', 'scenario': FIXED, 'settings': ONE_HUMAN})
        kinds = [m['type'] for m in self.got(out, 'ann')]
        self.assertEqual(kinds[:2], ['entered_game', 'feed'])
        gid = self.got(out, 'ann')[0]['game']['id']
        self.assertIn(gid, self.hub.runnable())

    def test_two_humans_open_a_lobby_seen_by_browsers(self):
        self.only('cat', {'type': 'browse'})
        out = self.send('ann', {'type': 'create_game', 'scenario': FIXED, 'settings': TWO_HUMANS})
        [lobby] = self.got(out, 'ann')
        [update] = self.got(out, 'cat')
        self.assertEqual(update['type'], 'open_games')
        self.assertEqual([g['id'] for g in update['games']], [lobby['game']['id']])
        self.assertEqual(lobby['game']['settings'], TWO_HUMANS)
        self.assertEqual([s['player_name'] for s in lobby['game']['seats'][:2]], [None, None])

    def test_the_scenario_is_named_by_the_server(self):
        out = self.send('ann', {'type': 'create_game', 'scenario': {'kind': 'shared', 'id': self.duel, 'name': 'Fake'},
                                'settings': TWO_HUMANS})
        self.assertEqual(self.got(out, 'ann')[0]['game']['scenario']['name'], 'Duel')
        sid = self.only('bob', {'type': 'save_scenario', 'scenario': {'name': 'Bobs', 'settings': new_settings()}})['selected']
        reply = self.only('ann', {'type': 'create_game', 'scenario': {'kind': 'own', 'id': sid}, 'settings': TWO_HUMANS})
        self.assertEqual(reply['problems'], [f'you have no scenario {sid!r}'])

    def test_bad_settings_are_refused(self):
        reply = self.only('ann', {'type': 'create_game', 'scenario': FIXED, 'settings': settings(seat('HUMAN'))})
        self.assertEqual(reply['type'], 'error')


class TestLobbies(LobbyTest):
    def test_players_join_by_code_take_seats_and_everyone_in_the_lobby_sees_it(self):
        game = self.lobby()
        lobby = self.only('bob', {'type': 'join_code', 'code': game['code'].lower()})
        self.assertEqual(lobby['game']['id'], game['id'])
        out = self.send('bob', {'type': 'take_seat', 'seat': 2})
        for key in ('ann', 'bob'):
            [view] = self.got(out, key)
            self.assertEqual(view['game']['seats'][1]['player_name'], 'Bob')
        self.assertEqual(self.got(out, 'cat'), [])  # not in the lobby, not browsing

    def test_a_taken_seat_or_a_seat_outside_a_lobby_is_refused(self):
        game = self.lobby()
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        self.only('bob', {'type': 'enter_lobby', 'game_id': game['id']})
        self.assertEqual(self.only('bob', {'type': 'take_seat', 'seat': 1})['problems'], ['seat 1 is taken'])
        self.assertEqual(self.only('cat', {'type': 'take_seat', 'seat': 2})['problems'], ['enter a game lobby first'])
        self.assertEqual(self.only('bob', {'type': 'take_seat', 'seat': 'two'})['problems'], ['which seat? (a number)'])

    def test_the_host_launches_when_full_and_the_players_enter_the_game(self):
        game = self.lobby()
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        self.only('bob', {'type': 'enter_lobby', 'game_id': game['id']})
        self.assertEqual(self.only('ann', {'type': 'launch'})['problems'], ['every human seat must be taken first (open: 2)'])
        self.send('bob', {'type': 'take_seat', 'seat': 2})
        self.assertEqual(self.only('bob', {'type': 'launch'})['problems'], ['only the host can launch the game'])
        out = self.send('ann', {'type': 'launch'})
        for key in ('ann', 'bob'):
            self.assertEqual(self.got(out, key), [{'type': 'game_launched', 'game_id': game['id']}])
        self.assertIn(game['id'], self.hub.sessions)
        entered = self.got(self.send('bob', {'type': 'enter_game', 'game_id': game['id']}), 'bob')[0]
        self.assertEqual(len(entered['my_factions']), 1)

    def test_the_host_cancels_and_the_game_leaves_the_list(self):
        game = self.lobby()
        self.only('bob', {'type': 'enter_lobby', 'game_id': game['id']})
        self.only('cat', {'type': 'browse'})
        out = self.send('ann', {'type': 'cancel'})
        self.assertEqual(self.got(out, 'bob'), [{'type': 'game_cancelled', 'game_id': game['id']}])
        self.assertEqual(self.got(out, 'cat')[0]['games'], [])
        self.assertEqual(self.only('bob', {'type': 'join_code', 'code': game['code']})['problems'], ['that game is cancelled'])

    def test_my_games_lists_live_and_forming_ones_i_am_in(self):
        self.send('ann', {'type': 'create_game', 'scenario': FIXED, 'settings': ONE_HUMAN})
        forming = self.lobby('bob')
        self.only('ann', {'type': 'enter_lobby', 'game_id': forming['id']})
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        reply = self.only('ann', {'type': 'my_games'})
        self.assertEqual(sorted(g['status'] for g in reply['games']), ['forming', 'live'])
        self.assertTrue(all(g['loadable'] for g in reply['games']))


class TestChat(LobbyTest):
    def test_the_browse_room_reaches_everyone_browsing(self):
        self.only('ann', {'type': 'browse'})
        self.only('bob', {'type': 'browse'})
        out = self.send('ann', {'type': 'chat', 'room': 'browse', 'text': 'duel anyone?'})
        [(keys, msg)] = out
        self.assertEqual(sorted(keys), ['ann', 'bob'])
        self.assertEqual((msg['message']['player_name'], msg['message']['text']), ('Ann', 'duel anyone?'))
        self.assertEqual(self.only('cat', {'type': 'browse'})['chat'][-1]['text'], 'duel anyone?')  # history

    def test_a_lobbys_chat_reaches_its_members_only(self):
        game = self.lobby()
        self.only('bob', {'type': 'enter_lobby', 'game_id': game['id']})
        self.only('cat', {'type': 'browse'})
        [(keys, _)] = self.send('bob', {'type': 'chat', 'room': game['id'], 'text': 'hi'})
        self.assertEqual(sorted(keys), ['ann', 'bob'])
        self.assertEqual(self.only('cat', {'type': 'chat', 'room': game['id'], 'text': 'x'})['problems'],
                         ["you can only chat in the lobby you're in"])
        self.assertEqual(self.only('bob', {'type': 'chat', 'room': 'browse', 'text': 'x'})['problems'],
                         ['browse the available games to chat there'])
        rejoin = self.only('cat', {'type': 'enter_lobby', 'game_id': game['id']})
        self.assertEqual([m['text'] for m in rejoin['chat']], ['hi'])


if __name__ == '__main__':
    unittest.main()
