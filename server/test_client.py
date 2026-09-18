"""
A minimal scripted WebSocket client -- joins as NAA against server.app's
demo game, submits one small purchase, confirms it, and prints every
message the server sends back. Not a test in the unittest sense (nothing
here is asserted); it's a manual sanity check that the actual network
loop works end to end, since server/tests/test_session.py deliberately
never opens a real socket.

Run the server first in one terminal: python -m server.app
Then, in another terminal:            python -m server.test_client
"""
import argparse
import asyncio
import json

import websockets


async def main(uri):
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({'type': 'join', 'faction': 'NAA'}))

        state = json.loads(await ws.recv())
        print('<-', state['type'])
        your_turn = json.loads(await ws.recv())
        print('<-', your_turn)

        gs = state['game_state']
        owned = next(tid for tid, t in gs['territories'].items() if t['owner'] == 'NAA')
        treasury = gs['factions']['NAA']['treasury_mpc']
        print(f'NAA owns territory {owned}, treasury {treasury} MPC')

        await ws.send(json.dumps({
            'type': 'submit_purchases', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': int(owned)}],
        }))
        print('<-', json.loads(await ws.recv()))

        await ws.send(json.dumps({'type': 'confirm_purchases', 'faction': 'NAA'}))
        while True:
            msg = json.loads(await ws.recv())
            print('<-', msg['type'], {k: v for k, v in msg.items() if k != 'game_state'})
            if msg['type'] in ('your_turn', 'game_over'):
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uri', default='ws://localhost:8765')
    args = parser.parse_args()
    asyncio.run(main(args.uri))
