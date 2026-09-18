"""
A minimal scripted WebSocket client -- joins as NAA against server.app's
demo game (NAA human, AAC bot), then drives two full NAA turns: a first
"purchase" (client-composed, one shot -- see server/session.py's own
docstring on why there's no separate stage/confirm round trip) followed
by a first "noncombat_move" (a real decision every turn, unlike Combat
Move) and "alliance_action" (always "none" here -- the demo's only 2
active factions, NAA and AAC, mean GameEngine._effective_max_alliance_
size caps out at 1, so no alliance could ever actually form anyway; see
GameEngine.legal_alliance_options); then a second turn's "purchase" and,
since allow_combat_moves_first_turn only skips Combat Move on a faction's
very own first turn (turns_taken == 0), a real "combat_move" decision on
that second turn too, followed by its own "noncombat_move" and
"alliance_action" -- picking whatever legal option the server's
"your_turn" offered each time (an attack/move if one exists, otherwise an
empty order list or "none"). Prints every message the server sends back
-- including AAC's whole bot turn, played back event by event with a
short pause between each, as a stand-in for a real client's own
playback-speed control (decided this session: pacing is entirely a
client-side concern once it has the full event list -- the server never
paces delivery itself). Not a test in the unittest sense (nothing here
is asserted); it's a manual sanity check that the actual network loop
works end to end, since server/tests/test_session.py deliberately never
opens a real socket.

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


async def _respond_to_your_turn(ws, msg, turn_number):
    """Composes and sends whatever this "your_turn" needs, client-side, the
    same way a real UI would: a purchase order list for PURCHASE (buys
    nothing on the first pass), a combat-move order list for COMBAT_MOVE,
    a non-combat-move order list for NONCOMBAT_MOVE -- each picking the
    first legal option offered for the first unit that has one, to
    demonstrate a real move, or an empty list if none exist -- or, for
    ALLIANCES, always "none" (see this module's own docstring on why)."""
    faction = msg['faction']
    phase = msg['phase']
    if phase == 'PURCHASE':
        print(f'NAA turn {turn_number}: purchasing nothing')
        await ws.send(json.dumps({'type': 'purchase', 'faction': faction, 'orders': []}))
    elif phase == 'COMBAT_MOVE':
        options = msg['legal_combat_moves']
        orders = []
        for unit_id_str, entry in options.items():
            if entry['destinations']:
                dest, path = next(iter(entry['destinations'].items()))
                orders.append({'unit_id': int(unit_id_str), 'path': path})
                break
        print(f'NAA turn {turn_number}: combat move orders: {orders or "(none)"}')
        await ws.send(json.dumps({'type': 'combat_move', 'faction': faction, 'orders': orders}))
    elif phase == 'NONCOMBAT_MOVE':
        options = msg['legal_noncombat_moves']
        orders = []
        for unit_id_str, entry in options.items():
            if entry['destinations']:
                orders.append({'unit_id': int(unit_id_str), 'destination': entry['destinations'][0]})
                break
        print(f'NAA turn {turn_number}: non-combat move orders: {orders or "(none)"}')
        await ws.send(json.dumps({'type': 'noncombat_move', 'faction': faction, 'orders': orders}))
    elif phase == 'ALLIANCES':
        print(f'NAA turn {turn_number}: alliance action: none')
        await ws.send(json.dumps({'type': 'alliance_action', 'faction': faction, 'action': 'none'}))
    else:
        raise AssertionError(f'unexpected your_turn phase: {phase}')


async def main(uri):
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({'type': 'join', 'faction': 'NAA'}))

        state = json.loads(await ws.recv())
        print('<-', state['type'])
        your_turn = json.loads(await ws.recv())
        print('<-', your_turn['type'], your_turn.get('phase'))

        gs = state['game_state']
        treasury = gs['factions']['NAA']['treasury_mpc']
        print(f'NAA treasury: {treasury} MPC')

        turn_number = 1
        await _respond_to_your_turn(ws, your_turn, turn_number)
        while True:
            msg = json.loads(await ws.recv())
            if msg['type'] in ('bot_turn', 'combat_events'):
                await _play_events(msg['events'], f"{msg['type']} ({msg.get('faction', '?')})")
                continue
            print('<-', msg['type'], {k: v for k, v in msg.items() if k not in (
                'game_state', 'events', 'legal_combat_moves', 'legal_noncombat_moves', 'legal_alliance_options',
            )})
            if msg['type'] == 'game_over':
                break
            if msg['type'] == 'your_turn':
                if msg['phase'] == 'PURCHASE':
                    turn_number += 1
                if turn_number > 2:
                    break
                await _respond_to_your_turn(ws, msg, turn_number)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uri', default='ws://localhost:8765')
    args = parser.parse_args()
    asyncio.run(main(args.uri))
