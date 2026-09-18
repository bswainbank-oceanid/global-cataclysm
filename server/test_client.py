"""
A minimal scripted WebSocket client -- joins as NAA against server.app's
demo game (NAA human, AAC bot), sends one complete "purchase" order list
(client-composed, one shot -- see server/session.py's own docstring on
why there's no separate stage/confirm round trip), then prints every
message the server sends back -- including AAC's whole bot turn, played
back event by event with a short pause between each, as a stand-in for a
real client's own playback-speed control (decided this session: pacing
is entirely a client-side concern once it has the full event list -- the
server never paces delivery itself). Not a test in the unittest sense
(nothing here is asserted); it's a manual sanity check that the actual
network loop works end to end, since server/tests/test_session.py
deliberately never opens a real socket.

Run the server first in one terminal: python -m server.app
Then, in another terminal:            python -m server.test_client
"""
import argparse
import asyncio
import json

import websockets

EVENT_PLAYBACK_DELAY_SECONDS = 0.15


def _describe(event):
    kind = event['kind']
    if kind == 'purchase':
        items = ', '.join(f"{o['qty']}x {o['unit_type']} -> territory {o['deploy_at']}" for o in event['orders'])
        return f"{event['faction']} buys: {items or '(nothing)'} (total {event['total_cost']} MPC)"
    if kind == 'combat_move':
        return f"{event['faction']} moves {len(event['orders'])} unit(s) to attack"
    if kind == 'noncombat_move':
        return f"{event['faction']} repositions {len(event['orders'])} unit(s)"
    if kind == 'battle_event':
        ek = event['event_kind']
        if ek == 'UNIT_ROLL':
            outcome = 'HIT' if event['hit'] else 'miss'
            return (f"  round {event['round_number']} @ territory {event['territory_id']}: "
                    f"{event['owner']}'s {event['unit_type']} rolls {event['roll']} -> {outcome}"
                    + (f", {event['damage']} dmg" if event['hit'] else ''))
        if ek == 'BATTLE_END':
            return f"  battle at territory {event['territory_id']} ends: {event['outcome']}"
        return f"  [{ek}] territory {event['territory_id']} round {event['round_number']}"
    if kind == 'territory_captured':
        return f"{event['faction']} captures territory {event['territory_id']} (from {event['previous_owner']})"
    if kind == 'unit_deployed':
        return f"{event['faction']} deploys {event['qty']}x {event['unit_type']} to territory {event['territory_id']}"
    if kind == 'income_collected':
        return f"{event['faction']} collects {event['amount']} MPC"
    if kind == 'faction_eliminated':
        return f"{event['faction']} is eliminated!"
    return str(event)


async def _play_events(events, label):
    print(f'--- {label} ({len(events)} events) ---')
    for event in events:
        print(_describe(event))
        await asyncio.sleep(EVENT_PLAYBACK_DELAY_SECONDS)  # stand-in for client-controlled playback pacing
    print('--- end ---')


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
            'type': 'purchase', 'faction': 'NAA',
            'orders': [{'unit_type': 'Infantry', 'qty': 1, 'deploy_at': int(owned)}],
        }))
        while True:
            msg = json.loads(await ws.recv())
            if msg['type'] in ('bot_turn', 'combat_events'):
                await _play_events(msg['events'], f"{msg['type']} ({msg.get('faction', '?')})")
                continue
            print('<-', msg['type'], {k: v for k, v in msg.items() if k not in ('game_state', 'events')})
            if msg['type'] in ('your_turn', 'game_over'):
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uri', default='ws://localhost:8765')
    args = parser.parse_args()
    asyncio.run(main(args.uri))
