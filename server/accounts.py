"""
Player accounts and logins (docs/DATA_MODEL.md, "User" and "Login"), on top of server/store.py.

    user, token = register(store, 'Brian', 'Brian S.', 'b@example.com', 'secret')
    user, token = login(store, 'B@Example.com', 'secret')     # the email ignores case
    user = login_with_token(store, token)                      # a client logging back in
    logout(store, token)

A user as returned here never carries the password: {id, player_name, actual_name, email, admin,
created}. Only a salted PBKDF2 hash of the password is stored, and only a SHA-256 of each login
token, so a copy of the database can't be used to log anyone in.
"""
import hashlib
import hmac
import secrets

from .store import StoreError, utc_now

SCHEME = 'pbkdf2_sha256'
ITERATIONS = 600_000  # stored with each hash, so raising it later leaves older accounts working
MAX_PLAYER_NAME = 24
MAX_ACTUAL_NAME = 60
MAX_EMAIL = 254
MAX_PASSWORD = 1024   # (hashing an enormous password would tie the server up)
WRONG_LOGIN = 'wrong email or password'
LOCKED = 'this account is locked: ask an admin'


def public(user):
    """`user` as the rest of the server and the client see it: everything but the password."""
    return {k: v for k, v in user.items() if k != 'password'}


def check_profile(player_name, actual_name, email, password):
    """The problems with a new account's details, in words a player can read (empty when it's fine)."""
    problems = []
    if not player_name:
        problems.append('a player name is needed')
    elif len(player_name) > MAX_PLAYER_NAME:
        problems.append(f'a player name may be at most {MAX_PLAYER_NAME} characters')
    elif not player_name.isprintable():
        problems.append('a player name may only use printable characters')
    if len(actual_name) > MAX_ACTUAL_NAME:
        problems.append(f'an actual name may be at most {MAX_ACTUAL_NAME} characters')
    local, at, domain = email.partition('@')
    if not email:
        problems.append('an email is needed')
    elif len(email) > MAX_EMAIL or not (local and at and domain) or any(c.isspace() for c in email) \
            or '@' in domain:
        problems.append('that is not an email address')
    if len(password) < 1:
        problems.append('a password needs at least one character')
    elif len(password) > MAX_PASSWORD:
        problems.append(f'a password may be at most {MAX_PASSWORD} characters')
    return problems


def register(store, player_name, actual_name, email, password):
    """Makes an account and logs it in: (user, token). Raises StoreError when a detail is missing or
    wrong, or the email or player name is taken (whatever its case)."""
    player_name, actual_name, email = str(player_name or '').strip(), str(actual_name or '').strip(), \
        str(email or '').strip()
    password = str(password or '')
    problems = check_profile(player_name, actual_name, email, password)
    if problems:
        raise StoreError(problems)
    from .admin import max_users  # (an admin's setting: the most players this server takes)
    limit = max_users(store)
    if len(store.fetch_all('users')) >= limit:
        raise StoreError(f'this server has room for {limit} players, and it is full')
    with store.transaction():
        uid = store.next_id('user')
        user = {'id': uid, 'player_name': player_name, 'actual_name': actual_name, 'email': email,
                'password': _hash(password), 'admin': False, 'created': utc_now()}
        store.insert('users', user, id=uid, email=email, player_name=player_name)
        token = _new_login(store, uid)
    return public(user), token


def login(store, email, password):
    """(user, token) for the right email and password; raises StoreError otherwise, without saying
    which of the two was wrong."""
    user = store.fetch('users', email=str(email or '').strip())
    password = str(password or '')
    if user is None:
        _hash(password)  # (as slow as a real check, so the time taken doesn't tell whether the email exists)
        raise StoreError(WRONG_LOGIN)
    if not _matches(password, user['password']):
        raise StoreError(WRONG_LOGIN)
    if user.get('locked'):
        raise StoreError(LOCKED)
    _note_login(store, user)
    return public(user), _new_login(store, user['id'])


def login_with_token(store, token):
    """The user a saved login token belongs to, or None if it isn't one (logged out, or never was)."""
    key = _token_hash(token)
    login_doc = store.fetch('logins', token_hash=key) if token else None
    if login_doc is None:
        return None
    user = store.fetch('users', id=login_doc['user_id'])
    if user is None or user.get('locked'):
        return None
    store.update('logins', {**login_doc, 'last_used': utc_now()}, {'token_hash': key})
    _note_login(store, user)
    return public(user)


def logout(store, token):
    """Ends the login `token` (on this client only: the account's other logins stay)."""
    if token:
        store.delete('logins', token_hash=_token_hash(token))


def get_user(store, user_id):
    user = store.fetch('users', id=user_id)
    return public(user) if user else None


def is_admin(store, user_id):
    user = store.fetch('users', id=user_id)
    return bool(user and user.get('admin'))


def find_user(store, who):
    """The user whose email or player name is `who` (either, ignoring case), or None."""
    who = str(who or '').strip()
    user = store.fetch('users', email=who) or store.fetch('users', player_name=who)
    return public(user) if user else None


def set_admin(store, user_id, admin=True):
    """Turns the admin flag on or off. (No UI: tools/set_admin.py.) Raises StoreError for an unknown user."""
    with store.transaction():
        user = store.fetch('users', id=user_id)
        if user is None:
            raise StoreError(f'there is no user {user_id!r}')
        user['admin'] = bool(admin)
        store.update('users', user, {'id': user_id})
    return public(user)


def _note_login(store, user):
    user['last_login'] = utc_now()
    store.update('users', user, {'id': user['id']})


# ---- passwords and tokens ------------------------------------------------------------------------

def _hash(password, salt=None, iterations=None):
    salt = salt if salt is not None else secrets.token_bytes(16)
    iterations = iterations or ITERATIONS
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return {'scheme': SCHEME, 'iterations': iterations, 'salt': salt.hex(), 'hash': digest.hex()}


def _matches(password, stored):
    if stored.get('scheme') != SCHEME:
        return False
    again = _hash(password, bytes.fromhex(stored['salt']), int(stored['iterations']))
    return hmac.compare_digest(again['hash'], stored['hash'])  # (constant time)


def _token_hash(token):
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def _new_login(store, user_id):
    token = secrets.token_urlsafe(32)
    now = utc_now()
    store.insert('logins', {'token_hash': _token_hash(token), 'user_id': user_id, 'created': now, 'last_used': now},
                 token_hash=_token_hash(token), user_id=user_id)
    return token
