"""
Limits on how fast anyone can try passwords or make accounts (server/hub.py checks them), so an open
server can't be used to guess a player's password or flood it with accounts.

    limits = Limits()
    limits.check('login', address, email)     # raises StoreError while either is over its limit
    limits.failed('login', address, email)    # a wrong password: counts against both
    limits.check('register', address)
    limits.failed('register', address)        # (every registration counts, successful or not)

Each kind of attempt allows so many per address (and per account, for logins) in a sliding window;
it's all in memory, so a restart forgets it (harmless: the windows are short).
"""
import time

from .store import StoreError

# kind -> (most attempts in the window, the window in seconds). Per address, and per account.
RULES = {
    'login': (10, 15 * 60),          # wrong passwords from one address
    'login_account': (5, 15 * 60),   # wrong passwords for one account, from anywhere
    'register': (5, 60 * 60),        # new accounts from one address
}


class Limits:
    def __init__(self, rules=None, clock=time.monotonic):
        self.rules = dict(RULES, **(rules or {}))
        self.clock = clock
        self._seen = {}  # (kind, who) -> [times of recent attempts]

    def check(self, kind, address, account=None):
        """Raises StoreError (with how long to wait) if `address`, or `account`, has used up `kind`."""
        for k, who in self._keys(kind, address, account):
            wait = self._wait(k, who)
            if wait > 0:
                minutes = max(1, int((wait + 59) // 60))
                raise StoreError(f"too many attempts: try again in {minutes} minute{'s' if minutes != 1 else ''}")

    def failed(self, kind, address, account=None):
        now = self.clock()
        for k, who in self._keys(kind, address, account):
            self._prune(k, who, now).append(now)

    def _keys(self, kind, address, account):
        keys = [(kind, address)] if address else []
        if account and f'{kind}_account' in self.rules:
            keys.append((f'{kind}_account', str(account).strip().lower()))
        return keys

    def _wait(self, kind, who):
        most, window = self.rules[kind]
        now = self.clock()
        times = self._prune(kind, who, now)
        return times[0] + window - now if len(times) >= most else 0

    def _prune(self, kind, who, now):
        window = self.rules[kind][1]
        times = [t for t in self._seen.get((kind, who), []) if t > now - window]
        if times:
            self._seen[(kind, who)] = times
        else:
            self._seen.pop((kind, who), None)
            times = self._seen.setdefault((kind, who), [])
        return times
