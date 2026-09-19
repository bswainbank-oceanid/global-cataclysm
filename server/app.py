"""
WebSocket server -- the actual network I/O, kept deliberately thin: every
real decision (what a message means, how it mutates the game) lives in
GameSession (server/session.py), which never touches a socket and is unit-
tested directly. This module's only two jobs are (1) turn parsed JSON
messages into GameSession.handle_message calls and (2) deliver whatever
messages come back to the right sockets -- a "to": faction_code key means
just that faction's connection(s), no "to" key means every connection on
this game (everyone sees the same board).

First vertical slice, per docs/GAME_ARCHITECTURE.md: ONE hardcoded game
(NAA and GPC both BOTs, every other faction NEUTRAL -- see
_build_demo_session), built fresh each time this process starts, with
randomize_play_order=False so NAA goes first. Clients connect as
watchers ({"type": "watch"}) and step the game a phase at a time with
{"type": "next"} -- see server/stepper.py. Not yet: multiple
simultaneous games, persistence, real auth (a "join" message is trusted
at face value for now).

Run: python -m server.app [--host HOST] [--port PORT]
A minimal scripted client for manual testing: python -m server.test_client
"""
import argparse
import asyncio
import json
import logging
import random

import websockets

from engine import data as data_module
from engine.bots.random_bot import RandomBot
from engine.engine import GameEngine
from engine.setup import build_game_state
from engine.state import FactionMode
from engine.turn_log import TurnLog
from .session import GameSession

logger = logging.getLogger('server')

WATCHER = '*watchers'  # sockets_by_faction key for spectators (not a faction code)


def _build_demo_session():
    modes = {code: FactionMode.NEUTRAL for code in data_module.factions()}
    modes['NAA'] = FactionMode.BOT
    modes['GPC'] = FactionMode.BOT
    gs = build_game_state('starting_setup_200ipc', modes, randomize_play_order=False)
    turn_log = TurnLog()
    engine = GameEngine(gs, data_module, turn_log=turn_log)
    bots = {code: RandomBot(engine, code, rng=random.Random()) for code in ('NAA', 'GPC')}
    return GameSession(engine, turn_log, bots)


class Server:
    def __init__(self, session):
        self.session = session
        self.sockets_by_faction = {}  # faction_code -> set[ServerConnection]

    async def handle_connection(self, websocket):
        """One coroutine per connected client, for its whole lifetime --
        websockets' own per-connection concurrency model, not something
        this class manages itself. `joined_as` is scoped to this one
        connection (a single socket could, in principle, never join, or
        join then get replaced by a later message -- the current code
        doesn't forbid that, matching this slice's "no real auth yet")."""
        joined_as = None
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await websocket.send(json.dumps({'type': 'error', 'message': 'malformed JSON'}))
                    continue

                if msg.get('type') == 'watch':
                    joined_as = WATCHER
                    self.sockets_by_faction.setdefault(joined_as, set()).add(websocket)
                elif msg.get('type') == 'join' and msg.get('faction'):
                    joined_as = msg['faction']
                    self.sockets_by_faction.setdefault(joined_as, set()).add(websocket)

                outgoing = self.session.handle_message(msg)
                await self._deliver(outgoing)
        except websockets.ConnectionClosed:
            pass
        finally:
            if joined_as is not None:
                self.sockets_by_faction.get(joined_as, set()).discard(websocket)

    async def _deliver(self, messages):
        for msg in messages:
            to = msg.get('to')
            if to:
                targets = self.sockets_by_faction.get(to, set())
            else:
                targets = {ws for sockets in self.sockets_by_faction.values() for ws in sockets}
            if not targets:
                continue
            payload = json.dumps(msg)
            await asyncio.gather(*(ws.send(payload) for ws in targets), return_exceptions=True)


async def main(host, port):
    session = _build_demo_session()
    server = Server(session)
    async with websockets.serve(server.handle_connection, host, port):
        logger.info('listening on ws://%s:%s', host, port)
        await asyncio.Future()  # run until killed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    asyncio.run(main(args.host, args.port))
