"""
Chat in the player database (docs/DATA_MODEL.md, "Chat"): the Available Games room, `browse`, for
everyone browsing it; one room per game lobby (the game's id); and, once a game is under way, its Game
Chat (game_room: everyone in the game) and one Ally Chat per alliance (ally_room: its members, whoever
they are at the time -- a faction joining reads what was said before, one leaving no longer can).

    message = post(store, 'browse', user_id, 'anyone up for a duel?')
    recent(store, 'browse')        # the last messages, oldest first
"""
from .store import StoreError, utc_now

BROWSE = 'browse'
MAX_TEXT = 500
RECENT = 50


def game_room(game_id):
    """A live game's Game Chat (a fresh room: the lobby's chat stays in the lobby)."""
    return f'{game_id}:game'


def ally_room(game_id, alliance):
    """The Ally Chat of one alliance in a game (`alliance`: its tag, never reused within a game)."""
    return f'{game_id}:ally:{alliance}'


def post(store, room, user_id, text):
    """Adds `user_id`'s message to `room`; returns it ({id, room, user_id, player_name, text, sent}). The
    player's name is copied in, so old messages read the same whatever happens to the account."""
    text = str(text or '').strip()
    if not text:
        raise StoreError('a chat message needs some text')
    if len(text) > MAX_TEXT:
        raise StoreError(f'a chat message may be at most {MAX_TEXT} characters')
    if room != BROWSE and store.fetch('games', id=str(room).split(':')[0]) is None:
        raise StoreError(f'there is no chat room {room!r}')
    user = store.fetch('users', id=user_id)
    if user is None:
        raise StoreError(f'there is no user {user_id!r}')
    message = {'room': room, 'user_id': user_id, 'player_name': user['player_name'], 'text': text,
               'sent': utc_now()}
    with store.transaction():
        message_id = store.insert('chat', message, room=room)
        message = {'id': message_id, **message}
        store.update('chat', message, {'id': message_id})  # (the id belongs in the document too)
    return message


def recent(store, room, limit=RECENT):
    """`room`'s last `limit` messages, oldest first (what a player sees on entering it)."""
    return list(reversed(store.fetch_all('chat', order='id DESC', limit=limit, room=room)))
