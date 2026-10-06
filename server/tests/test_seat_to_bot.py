"""Seats handed over to bots (server/hub.py hand_over / replace_with_bot, server/session.py convert_to_bot):
a player giving up their seat, one replaced for going over the game's turn_hours, and a locked account."""
import unittest

from engine.state import FactionMode
from server import accounts, games, persist
from server.hub import Hub, _duration
from server.lobby import check_settings
from server.tests.test_hub import TWO_HUMANS, settings
from server.tests.test_hub_lobby import LobbyTest
from server.tests.test_persist import bot, seat

TIMED = settings(seat('HUMAN'), seat('HUMAN'), bot(), seed=62, turn_hours=2)
LONG_AGO = '2020-01-01T00:00:00Z'


class SeatTest(LobbyTest):
    def live(self, s=TWO_HUMANS):
        """Ann hosts a game with Bob (seats 1 and 2), launched and run until it waits for one of them."""
        game = self.lobby(s=s)
        self.send('ann', {'type': 'take_seat', 'seat': 1})
        self.only('bob', {'type': 'enter_lobby', 'game_id': game['id']})
        self.send('bob', {'type': 'take_seat', 'seat': 2})
        self.send('ann', {'type': 'launch'})
        for key in ('ann', 'bob'):
            self.send(key, {'type': 'enter_game', 'game_id': game['id']})
        self.run_game(game['id'])
        return game['id']

    def run_game(self, game_id):
        while game_id in self.hub.runnable():
            self.hub.step(game_id)

    def faction(self, game_id, user_id):
        [f] = Hub._factions_of(games.get(self.store, game_id), user_id)
        return f

    def session(self, game_id):
        return self.hub.sessions[game_id]

    def waiting_player(self, game_id):
        """('ann'|'bob', faction) of the player the game is waiting for."""
        [f] = self.session(game_id).waiting_for()
        return ('ann', f) if f == self.faction(game_id, self.ann) else ('bob', f)

    def turn_began(self, game_id, when):
        record = games.get(self.store, game_id)
        games.update_progress(self.store, game_id, dict(record['progress'], turn_started=when))


class TestHandingOver(SeatTest):
    def test_a_player_hands_their_seat_to_a_bot_and_leaves_the_game(self):
        gid = self.live()
        bob_f = self.faction(gid, self.bob)
        out = self.send('bob', {'type': 'hand_over', 'faction': bob_f})
        changed = [m for m in self.got(out, 'ann') if m.get('type') == 'seat_changed']
        self.assertEqual([(m['faction'], m['reason']) for m in changed], [(bob_f, 'handed_over')])
        self.assertIn({'type': 'left_game', 'reason': 'you handed your seat over to a bot'}, self.got(out, 'bob'))
        gs = self.session(gid).engine.game_state
        self.assertEqual(gs.factions[bob_f].mode, FactionMode.BOT)
        self.assertIsNotNone(gs.factions[bob_f].alliance_strategy)
        record = games.get(self.store, gid)
        seat2 = next(s for s in record['seats'] if s['seat'] == 2)
        self.assertEqual((seat2['mode'], seat2['user_id'], seat2['handed_over']['user_id']), ('BOT', None, self.bob))
        self.assertEqual(self.only('bob', {'type': 'enter_game', 'game_id': gid})['message'], 'you are not playing in that game')
        self.run_game(gid)  # the bot plays bob's turns now: the game only ever waits for ann
        self.assertEqual(self.session(gid).waiting_for(), [self.faction(gid, self.ann)])

    def test_only_your_own_faction_and_never_the_internal_message(self):
        gid = self.live()
        bob_f = self.faction(gid, self.bob)
        self.assertEqual(self.only('ann', {'type': 'hand_over', 'faction': bob_f})['problems'], [f'you are not playing {bob_f}'])
        reply = self.only('bob', {'type': 'convert_to_bot', 'faction': bob_f, 'seed': 1})
        self.assertEqual(reply['message'], "unknown message type: 'convert_to_bot'")
        self.assertEqual(self.session(gid).engine.game_state.factions[bob_f].mode, FactionMode.HUMAN)

    def test_a_reloaded_game_has_the_same_bot(self):
        gid = self.live()
        key, f = self.waiting_player(gid)
        self.send(key, {'type': 'hand_over', 'faction': f})  # mid-turn: the bot finishes it
        self.run_game(gid)
        again = persist.load(games.load_state(self.store, gid)['session'])
        self.assertEqual(again.engine.game_state.to_dict(), self.session(gid).engine.game_state.to_dict())
        self.assertEqual(again.engine.game_state.factions[f].mode, FactionMode.BOT)

    def test_an_armistice_waiting_on_the_seat_is_accepted_by_its_bot(self):
        gid = self.live()
        ann_f, bob_f = self.faction(gid, self.ann), self.faction(gid, self.bob)
        self.send('ann', {'type': 'propose_armistice', 'faction': ann_f})
        self.assertEqual(self.session(gid).waiting_for(), [bob_f])
        out = self.send('bob', {'type': 'hand_over', 'faction': bob_f})
        self.assertIn('armistice_resolved', [m['type'] for m in self.got(out, 'ann')])
        self.assertTrue(self.session(gid).engine.game_state.game_over)
        self.assertEqual(games.get(self.store, gid)['status'], games.FINISHED)

    def test_a_locked_players_seats_go_to_bots(self):
        gid = self.live()
        accounts.set_admin(self.store, self.ann)
        bob_f = self.faction(gid, self.bob)
        out = self.send('ann', {'type': 'admin_lock', 'user_id': self.bob, 'locked': True})
        self.assertTrue(any(m.get('type') == 'seat_changed' and m['reason'] == 'locked' for m in self.got(out, 'ann')))
        self.assertEqual(self.session(gid).engine.game_state.factions[bob_f].mode, FactionMode.BOT)


class TestReplacing(SeatTest):
    def test_the_host_replaces_a_player_once_their_turn_runs_over(self):
        gid = self.live(TIMED)
        key, f = self.waiting_player(gid)
        while key == 'ann':  # (make it bob's turn: ann plays on)
            self.send('ann', {'type': 'end_phase', 'faction': f})
            self.run_game(gid)
            key, f = self.waiting_player(gid)
        problems = self.only('ann', {'type': 'replace_with_bot'})['problems'][0]
        self.assertRegex(problems, f'^{f} still has (2 hours|1 hour 59 minutes) to take their turn$')
        self.turn_began(gid, LONG_AGO)
        self.assertEqual(self.only('bob', {'type': 'replace_with_bot'})['problems'],
                         ['it is your own turn (hand your seat over instead)'])
        out = self.send('ann', {'type': 'replace_with_bot'})
        self.assertIn({'type': 'left_game', 'reason': 'a bot has taken your seat: your turn went over the time limit'},
                      self.got(out, 'bob'))
        self.assertEqual(self.session(gid).engine.game_state.factions[f].mode, FactionMode.BOT)

    def test_only_the_host_may_replace_unless_the_host_is_slow(self):
        gid = self.live(TIMED)
        key, f = self.waiting_player(gid)
        self.turn_began(gid, LONG_AGO)
        if key == 'bob':
            self.assertEqual(self.only('cat', {'type': 'replace_with_bot'})['message'], 'you are not in a live game')
            return  # (the host may: the test above)
        out = self.send('bob', {'type': 'replace_with_bot'})  # ann, the host, is the slow one
        self.assertEqual(self.session(gid).engine.game_state.factions[f].mode, FactionMode.BOT)
        self.assertNotIn('left_game', [m['type'] for m in self.got(out, 'ann')])  # (the host still watches)

    def test_no_limit_no_replacing(self):
        gid = self.live()
        self.turn_began(gid, LONG_AGO)
        self.assertEqual(self.only('ann', {'type': 'replace_with_bot'})['problems'], ['this game has no time limit on turns'])

    def test_the_turn_clock_starts_with_each_turn(self):
        gid = self.live(TIMED)
        progress = games.get(self.store, gid)['progress']
        self.assertTrue(progress['turn_started'].endswith('Z'))
        self.turn_began(gid, LONG_AGO)
        self.hub._save(gid)  # the same turn: the clock keeps running
        self.assertEqual(games.get(self.store, gid)['progress']['turn_started'], LONG_AGO)


class TestTurnHoursSetting(unittest.TestCase):
    def test_turn_hours_is_a_whole_number_of_hours(self):
        self.assertEqual(check_settings(dict(TWO_HUMANS, turn_hours=24), max_humans=None), [])
        for bad in (-1, 721, 'day', 1.5, True):
            self.assertEqual(check_settings(dict(TWO_HUMANS, turn_hours=bad), max_humans=None),
                             ['turn_hours must be a whole number from 0 to 720'])

    def test_time_left_reads_well(self):
        self.assertEqual(_duration(7200), '2 hours')
        self.assertEqual(_duration(3600 + 125), '1 hour 3 minutes')
        self.assertEqual(_duration(20), '1 minute')


if __name__ == '__main__':
    unittest.main()
