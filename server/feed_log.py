"""
A game's playback, kept for good: every numbered message its session broadcast (server/session.py's feed -- a
phase result, the next step queued, the state, the game's end), in order, across server restarts (each run of
the server numbers its own: an "epoch" and a "seq" from 1). The session itself keeps only the latest few hundred,
in memory; this is what lets a player who was away -- even while the server restarted, even after the game has
ended -- play back from where they left off to the end.

Also where each player has watched up to (their client's "played" reports), and whether they've seen the end:
a finished game stays in their Resume list until they have.

Tables (server/store.py, schema version 5):
    game_feed     (game_id, epoch, seq, kind: the message's type) + doc {"z": the message, compressed}, in
                  broadcast order (rowid)
    game_viewers  (game_id, user_id) + doc {"epoch", "seq", "done"}
"""
import base64
import json
import zlib


def _pack(message):
    return {'z': base64.b64encode(zlib.compress(json.dumps(message).encode('utf-8'))).decode('ascii')}


def _unpack(doc):
    return json.loads(zlib.decompress(base64.b64decode(doc['z'])))


def append(store, game_id, message):
    """Keeps one numbered broadcast (it has "seq" and "epoch")."""
    store.insert('game_feed', _pack(message), game_id=game_id, epoch=str(message['epoch']), seq=int(message['seq']),
                 kind=str(message.get('type', '')))


def _position(store, game_id, epoch, seq):
    """Where message (`epoch`, `seq`) falls in the game's playback (its row), or None."""
    got = store.query('SELECT rowid FROM game_feed WHERE game_id = ? AND epoch = ? AND seq = ?', (game_id, str(epoch), int(seq)))
    return got[0]['rowid'] if got else None


def _last_of(store, game_id, kind, upto=None):
    sql = 'SELECT doc FROM game_feed WHERE game_id = ? AND kind = ?' + (' AND rowid <= ?' if upto is not None else '')
    got = store.query(sql + ' ORDER BY rowid DESC LIMIT 1', (game_id, kind) + ((upto,) if upto is not None else ()))
    return _unpack(got[0]['doc']) if got else None


def since(store, game_id, epoch, seq):
    """Everything broadcast after message (`epoch`, `seq`) -- through any later runs of the server -- with the
    state and the step queued as they stood at that point: {"messages", "state", "queue"}. None when that message
    isn't kept (another game's, say)."""
    at = _position(store, game_id, epoch, seq)
    if at is None:
        return None
    rows = store.query('SELECT doc FROM game_feed WHERE game_id = ? AND rowid > ? ORDER BY rowid', (game_id, at))
    return {'messages': [_unpack(r['doc']) for r in rows], 'state': _last_of(store, game_id, 'state', at),
            'queue': _last_of(store, game_id, 'phase_queue', at)}


def last_result(store, game_id):
    """(epoch, seq) of the last phase result broadcast -- what a player has seen the end by -- or None."""
    got = store.query("SELECT epoch, seq FROM game_feed WHERE game_id = ? AND kind = 'phase_result' "
                      'ORDER BY rowid DESC LIMIT 1', (game_id,))
    return (got[0]['epoch'], got[0]['seq']) if got else None


def watched(store, game_id, user_id):
    """{epoch, seq, done} -- how far `user_id` has played this game back -- or None."""
    return store.fetch('game_viewers', game_id=game_id, user_id=user_id)


def note_played(store, game_id, user_id, epoch, seq, game_over=False):
    """`user_id`'s client has played the game back to message (`epoch`, `seq`): kept if it's further than before.
    Done once that's the game's last phase result and the game is over."""
    mine = watched(store, game_id, user_id)
    here = _position(store, game_id, epoch, seq)
    if here is None:
        return
    if mine is not None and not mine.get('done'):
        before = _position(store, game_id, mine['epoch'], mine['seq'])
        if before is not None and before >= here:
            return
    elif mine is not None and mine.get('done'):
        return
    done = bool(game_over) and last_result(store, game_id) == (str(epoch), int(seq))
    store.upsert('game_viewers', {'game_id': game_id, 'user_id': user_id, 'epoch': str(epoch), 'seq': int(seq),
                                  'done': done}, game_id=game_id, user_id=user_id)


def note_seen_end(store, game_id, user_id):
    """`user_id` has seen this (finished) game's end -- its Game Over report shown to them, live or played back:
    done, whatever position they'd played back to."""
    mine = watched(store, game_id, user_id)
    epoch, seq = (mine['epoch'], mine['seq']) if mine else (last_result(store, game_id) or ('', 0))
    store.upsert('game_viewers', {'game_id': game_id, 'user_id': user_id, 'epoch': str(epoch), 'seq': int(seq),
                                  'done': True}, game_id=game_id, user_id=user_id)


def seen_the_end(store, game_id, user_id):
    mine = watched(store, game_id, user_id)
    return bool(mine and mine.get('done'))
