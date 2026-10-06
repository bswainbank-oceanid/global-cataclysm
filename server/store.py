"""
The server's player data -- accounts, logins, players' own scenarios and settings, games,
game saves and chat -- in one SQLite file (docs/DATA_MODEL.md, "Player data"). Game content
(the modules under data/modules) is not here: this is what players make while using the game.

Each record is a JSON document in a `doc` column; the fields the server looks records up by
are copied into columns of their own, so the database can enforce uniqueness (an email, a
player name, a game's code) and find things quickly. Everything goes through Store, so the
database can be swapped later without the rest of the server noticing.

    store = Store()                                  # server_data/global_cataclysm.sqlite3
    store = Store(':memory:')                        # tests
    uid = store.next_id('user')                      # 'U_000001'
    store.insert('users', {...doc...}, id=uid, email=..., player_name=...)
    doc = store.fetch('users', id=uid)
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'server_data' / 'global_cataclysm.sqlite3'

# Bump when the tables change, with an upgrade step in Store._upgrade.
SCHEMA_VERSION = 2

# Server-assigned ids: a prefix per kind of record, then a running number.
ID_PREFIXES = {'user': 'U_', 'scenario': 'S_', 'game': 'G_'}

SCHEMA = """
CREATE TABLE users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    player_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    doc         TEXT NOT NULL
);
CREATE TABLE logins (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc        TEXT NOT NULL
);
CREATE INDEX logins_by_user ON logins(user_id);
CREATE TABLE scenarios (
    id       TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT NOT NULL COLLATE NOCASE,
    doc      TEXT NOT NULL,
    UNIQUE (owner_id, name)
);
CREATE TABLE user_settings (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL,
    doc         TEXT NOT NULL,
    PRIMARY KEY (user_id, scenario_id)
);
CREATE TABLE games (
    id      TEXT PRIMARY KEY,
    code    TEXT NOT NULL UNIQUE,
    host_id TEXT NOT NULL REFERENCES users(id),
    status  TEXT NOT NULL,
    doc     TEXT NOT NULL
);
CREATE INDEX games_by_status ON games(status);
CREATE INDEX games_by_host ON games(host_id);
CREATE TABLE game_saves (
    game_id TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    doc     TEXT NOT NULL
);
CREATE TABLE chat (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    doc  TEXT NOT NULL
);
CREATE INDEX chat_by_room ON chat(room, id);
CREATE TABLE server_settings (
    key TEXT PRIMARY KEY,
    doc TEXT NOT NULL
);
CREATE TABLE counters (
    kind TEXT PRIMARY KEY,
    last INTEGER NOT NULL
);
"""

# What each schema version added, for a database an older server made: [(version, [statements])].
UPGRADES = [
    (2, ['CREATE TABLE server_settings (key TEXT PRIMARY KEY, doc TEXT NOT NULL)']),
]

# A unique column that is already taken, as the player should read it.
TAKEN = {
    'users.email': 'that email is already registered',
    'users.player_name': 'that player name is already taken',
    'scenarios.owner_id, scenarios.name': 'you already have a scenario with that name',
    'games.code': 'that game code is already in use',
}


class StoreError(Exception):
    """A request the store refuses, in words a player can read (like lobby.LobbyError)."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__('; '.join(self.problems))


def utc_now():
    """Now, as stored: UTC, ISO 8601, to the second ('2026-10-04T15:00:00Z')."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Store:
    def __init__(self, path=DEFAULT_PATH):
        """Opens the database at `path` (':memory:' for one that lives only as long as this Store),
        creating the file, its folder and its tables if they are missing."""
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit off by hand: each write is its own transaction unless inside transaction(). Any thread
        # may use it (the server runs a game's steps on a worker thread), one at a time: callers serialize.
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = ON')
        if str(path) != ':memory:':
            self._db.execute('PRAGMA journal_mode = WAL')  # readers don't wait for a writer
        self._depth = 0
        self._upgrade()

    def close(self):
        self._db.close()

    def _upgrade(self):
        version = self._db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f'the player database is version {version}, newer than this server ({SCHEMA_VERSION})')
        if version == 0:
            with self.transaction():  # (statement by statement: executescript would commit on its own)
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        self._db.execute(statement)
                self._db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            return
        for to, statements in UPGRADES:  # a database from an older server: bring it up to date
            if version < to:
                with self.transaction():
                    for statement in statements:
                        self._db.execute(statement)
                    self._db.execute(f'PRAGMA user_version = {to}')
                version = to

    @contextmanager
    def transaction(self):
        """Groups writes: all of them happen, or (on an exception) none. Nests: only the outermost
        one commits."""
        if self._depth == 0:
            self._db.execute('BEGIN IMMEDIATE')
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._db.execute('ROLLBACK')
            raise
        self._depth -= 1
        if self._depth == 0:
            self._db.execute('COMMIT')

    # ---- ids --------------------------------------------------------------------------------------

    def next_id(self, kind):
        """The next id of a kind of record ('user' -> 'U_000001', 'U_000002', ...). Never reused, even
        after a delete."""
        prefix = ID_PREFIXES[kind]
        with self.transaction():
            self._db.execute('INSERT INTO counters (kind, last) VALUES (?, 1) '
                             'ON CONFLICT(kind) DO UPDATE SET last = last + 1', (kind,))
            last = self._db.execute('SELECT last FROM counters WHERE kind = ?', (kind,)).fetchone()[0]
        return f'{prefix}{last:06d}'

    # ---- documents --------------------------------------------------------------------------------

    def insert(self, table, doc, **columns):
        """Adds a record: `doc` as its JSON, `columns` its lookup columns (keys included). Returns the
        new row's id for tables that number their rows (chat). A unique column already taken raises
        StoreError."""
        names = list(columns) + ['doc']
        values = list(columns.values()) + [_dump(doc)]
        sql = f'INSERT INTO {table} ({", ".join(names)}) VALUES ({", ".join("?" * len(names))})'
        with self._unique(table):
            return self._db.execute(sql, values).lastrowid

    def update(self, table, doc, key, **columns):
        """Replaces the record whose key columns are `key` (a dict) with `doc`, and sets `columns`.
        Returns whether there was one."""
        sets = [f'{c} = ?' for c in columns] + ['doc = ?']
        values = list(columns.values()) + [_dump(doc)]
        where, args = _where(key)
        with self._unique(table):
            return self._db.execute(f'UPDATE {table} SET {", ".join(sets)} WHERE {where}', values + args).rowcount > 0

    def upsert(self, table, doc, **key):
        """Writes the record whose key columns are `key`, adding it if it isn't there."""
        if not self.update(table, doc, key):
            self.insert(table, doc, **key)

    def fetch(self, table, **key):
        """The document of the one record matching `key`, or None."""
        where, args = _where(key)
        row = self._db.execute(f'SELECT doc FROM {table} WHERE {where}', args).fetchone()
        return json.loads(row['doc']) if row else None

    def fetch_all(self, table, order='rowid', limit=None, **where_columns):
        """The documents of every record matching `where_columns` (a list value matches any of
        its items), ordered by `order` (a column, 'x DESC' for newest first)."""
        where, args = _where(where_columns) if where_columns else ('1', [])
        sql = f'SELECT doc FROM {table} WHERE {where} ORDER BY {order}'
        if limit is not None:
            sql += f' LIMIT {int(limit)}'
        return [json.loads(r['doc']) for r in self._db.execute(sql, args)]

    def delete(self, table, **key):
        """Removes the records matching `key`; returns how many there were."""
        where, args = _where(key)
        with self.transaction():
            return self._db.execute(f'DELETE FROM {table} WHERE {where}', args).rowcount

    @contextmanager
    def _unique(self, table):
        try:
            with self.transaction():
                yield
        except sqlite3.IntegrityError as e:
            message = str(e)
            if message.startswith('UNIQUE constraint failed: '):
                columns = message[len('UNIQUE constraint failed: '):]
                raise StoreError(TAKEN.get(columns, f'{columns} is already taken')) from None
            if message.startswith('FOREIGN KEY constraint failed'):
                raise StoreError(f'that refers to something in {table} that does not exist') from None
            raise


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(',', ':'))


def _where(columns):
    parts, args = [], []
    for name, value in columns.items():
        if isinstance(value, (list, tuple, set)):
            value = list(value)
            parts.append(f'{name} IN ({", ".join("?" * len(value))})' if value else '0')
            args += value
        else:
            parts.append(f'{name} = ?')
            args.append(value)
    return ' AND '.join(parts), args
