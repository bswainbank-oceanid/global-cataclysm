"""Chat in a game (server/hub.py game_chat / game_chat_history): the Game Chat and each alliance's Ally Chat."""
import unittest

from server import chat
from server.tests.test_hub import settings
from server.tests.test_persist import bot, seat
from server.tests.test_seat_to_bot import SeatTest

THREE_HUMANS = settings(seat('HUMAN'), seat('HUMAN'), seat('HUMAN'), seed=63)


class ChatTest(SeatTest):
    def three(self):
        """Ann hosts a game with Bob and Cat (seats 1-3), everyone in it."""
        game = self.lobby(s=THREE_HUMANS)
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        for key, n in (('bob', 2), ('cat', 3)):
            self.only(key, {'type': 'enter_lobby', 'game_id': game['id']})
            self.send(key, {'type': 'take_seat', 'seat': n})
        self.send('ann', {'type': 'launch'})
        for key in ('ann', 'bob', 'cat'):
            self.send(key, {'type': 'enter_game', 'game_id': game['id']})
        self.run_game(game['id'])
        return game['id']

    def ally(self, gid, user_ids, tag):
        factions = self.session(gid).engine.game_state.factions
        for f in factions.values():
            if f.alliance == tag:
                f.alliance = None
        for uid in user_ids:
            factions[self.faction(gid, uid)].alliance = tag

    def say(self, key, channel, text):
        return self.send(key, {'type': 'game_chat', 'channel': channel, 'text': text})


class TestGameChat(ChatTest):
    def test_everyone_in_the_game_hears_it_and_it_starts_fresh(self):
        game = self.lobby(s=THREE_HUMANS)
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        self.send('ann', {'type': 'chat', 'room': game['id'], 'text': 'lobby talk'})
        for key, n in (('bob', 2), ('cat', 3)):
            self.only(key, {'type': 'enter_lobby', 'game_id': game['id']})
            self.send(key, {'type': 'take_seat', 'seat': n})
        self.send('ann', {'type': 'launch'})
        for key in ('ann', 'bob'):
            self.send(key, {'type': 'enter_game', 'game_id': game['id']})
        self.assertEqual(self.only('ann', {'type': 'game_chat_history'})['game'], [])  # (not the lobby's)
        out = self.say('bob', 'game', 'hello all')
        for key in ('ann', 'bob'):
            [m] = self.got(out, key)
            self.assertEqual((m['channel'], m['message']['player_name'], m['message']['text']), ('game', 'Bob', 'hello all'))
        self.assertEqual(self.got(out, 'cat'), [])  # (not in the game yet)
        self.send('cat', {'type': 'enter_game', 'game_id': game['id']})
        self.assertEqual([m['text'] for m in self.only('cat', {'type': 'game_chat_history'})['game']], ['hello all'])

    def test_a_host_without_a_seat_reads_and_posts(self):
        s = settings(seat('HUMAN'), seat('HUMAN'), bot(), seed=64)
        game = self.lobby(s=s)
        for key, n in (('bob', 1), ('cat', 2)):
            self.only(key, {'type': 'enter_lobby', 'game_id': game['id']})
            self.send(key, {'type': 'take_seat', 'seat': n})
        self.send('ann', {'type': 'launch'})
        for key in ('ann', 'bob'):
            self.send(key, {'type': 'enter_game', 'game_id': game['id']})
        out = self.say('ann', 'game', 'the host speaks')
        self.assertEqual(len(self.got(out, 'bob')), 1)
        self.assertEqual(self.only('ann', {'type': 'game_chat', 'channel': 'ally', 'text': 'x'})['problems'],
                         ['you are not in an alliance'])

    def test_outside_a_game_or_saying_nothing_is_refused(self):
        self.assertEqual(self.only('cat', {'type': 'game_chat', 'channel': 'game', 'text': 'hi'})['problems'],
                         ['you are not in a live game'])
        self.three()
        self.assertEqual(self.only('cat', {'type': 'game_chat', 'channel': 'game', 'text': '  '})['problems'],
                         ['a chat message needs some text'])
        self.assertEqual(self.only('cat', {'type': 'game_chat', 'channel': 'team', 'text': 'x'})['problems'],
                         ["a game's chat is 'game' or 'ally'"])


class TestAllyChat(ChatTest):
    def test_only_the_alliance_hears_it(self):
        gid = self.three()
        self.ally(gid, [self.ann, self.bob], 'ALLIANCE_7')
        out = self.say('ann', 'ally', 'just between us')
        self.assertEqual(len(self.got(out, 'bob')), 1)
        self.assertEqual(self.got(out, 'ann')[0]['alliance'], 'ALLIANCE_7')
        self.assertEqual(self.got(out, 'cat'), [])
        self.assertEqual(self.only('cat', {'type': 'game_chat_history'})['ally'], None)

    def test_joining_reads_what_was_said_and_leaving_loses_it(self):
        gid = self.three()
        self.ally(gid, [self.ann, self.bob], 'ALLIANCE_7')
        self.say('ann', 'ally', 'before cat')
        self.ally(gid, [self.ann, self.bob, self.cat], 'ALLIANCE_7')  # cat joins
        history = self.only('cat', {'type': 'game_chat_history'})
        self.assertEqual((history['alliance'], [m['text'] for m in history['ally']]), ('ALLIANCE_7', ['before cat']))
        self.ally(gid, [self.ann, self.cat], 'ALLIANCE_7')  # bob leaves
        self.assertIsNone(self.only('bob', {'type': 'game_chat_history'})['ally'])
        self.ally(gid, [self.bob, self.cat], 'ALLIANCE_8')  # a new alliance: a new room
        self.assertEqual(self.only('bob', {'type': 'game_chat_history'})['ally'], [])

    def test_rooms_belong_to_a_game(self):
        gid = self.three()
        self.assertEqual(chat.game_room(gid), f'{gid}:game')
        self.assertEqual(chat.ally_room(gid, 'ALLIANCE_1'), f'{gid}:ally:ALLIANCE_1')


if __name__ == '__main__':
    unittest.main()
