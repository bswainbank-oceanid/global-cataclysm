"""
What a player can start a game from, and what they may change (docs/DATA_MODEL.md, "Scenarios:
shared, own, and a player's settings"):

- the built-ins: GC72 (`fixed`) and a blank New Scenario (`new`), whose defaults the client builds
  itself -- except that an admin may save GC72's editable settings (seats, alliances, rules) as everyone's
  defaults for it (setups.save_fixed);
- shared scenarios: the saved setups (server/setups.py, ScenarioSetup modules), which everyone sees and
  only an admin changes;
- a player's own scenarios, in the player database, which only their owner sees and changes.

A player may Save their own settings for a built-in or a shared scenario (a personal layer, used from
then on) and Reset Settings back to the scenario's own. An own scenario is changed directly: Save
updates it, or makes a new one when the name is new. Every refusal is a StoreError.
"""
from engine.repository import default_repository

from . import accounts, setups
from .lobby import GENERATOR_ID, LobbyError
from .store import StoreError, utc_now

BUILT_IN = ('fixed', 'new')  # the launcher's GC72 and New Scenario entries
BUILT_IN_KINDS = {'fixed': ('fixed',), 'new': ('new',)}


def listing(store, user_id, repo=None):
    """Everything `user_id` can pick in New Game, in the launcher's order -- the built-ins, the shared
    scenarios, then their own -- each {id, kind: 'fixed' | 'new' | 'shared' | 'own', name, description,
    settings, defaults, personal}. `settings` is what New Game fills in: the player's saved settings when
    there are some (`personal` true), else `defaults` (null for a built-in: the client's own defaults)."""
    repo = repo or default_repository()
    mine = {d['scenario_id']: d['settings'] for d in store.fetch_all('user_settings', user_id=user_id)}

    def entry(sid, kind, name, description, defaults):
        personal = sid in mine
        return {'id': sid, 'kind': kind, 'name': name, 'description': description,
                'settings': mine[sid] if personal else defaults, 'defaults': defaults, 'personal': personal}

    out = [entry('fixed', 'fixed', None, '', setups.fixed_defaults(repo)), entry('new', 'new', None, '', None)]
    out += [entry(s['id'], 'shared', s['name'], s['description'], s['settings']) for s in setups.listing(repo)]
    own = sorted(store.fetch_all('scenarios', owner_id=user_id), key=lambda d: (d['name'].lower(), d['id']))
    out += [{'id': d['id'], 'kind': 'own', 'name': d['name'], 'description': d['description'],
             'settings': d['settings'], 'defaults': d['settings'], 'personal': False} for d in own]
    return out


# ---- a player's settings for a built-in or shared scenario -------------------------------------------

def save_settings(store, user_id, scenario_id, settings, repo=None):
    """Saves `user_id`'s own settings for the built-in or shared scenario `scenario_id`; returns them as
    saved (tidied: no seed or dev switches)."""
    kinds = _kinds_for(scenario_id, repo)
    problems = setups.checked_settings_problems(settings, kinds)
    if problems:
        raise StoreError(problems)
    settings = setups.tidy(settings)
    store.upsert('user_settings', {'user_id': user_id, 'scenario_id': scenario_id, 'settings': settings,
                                   'saved': utc_now()}, user_id=user_id, scenario_id=scenario_id)
    return settings


def reset_settings(store, user_id, scenario_id):
    """Reset Settings: forgets `user_id`'s own settings for `scenario_id` (the scenario's own come back).
    Returns whether there were any."""
    return store.delete('user_settings', user_id=user_id, scenario_id=scenario_id) > 0


def _kinds_for(scenario_id, repo):
    if scenario_id in BUILT_IN_KINDS:
        return BUILT_IN_KINDS[scenario_id]
    if scenario_id in {s['id'] for s in setups.listing(repo or default_repository())}:
        return ('new',)
    if isinstance(scenario_id, str) and scenario_id.startswith('S_'):
        raise StoreError('your own scenarios are saved as they are, not with separate settings')
    raise StoreError(f'there is no scenario {scenario_id!r}')


# ---- a player's own scenarios ------------------------------------------------------------------------

def save_own(store, user_id, scenario):
    """Saves `scenario` ({id: the own scenario being edited or null, name, description, settings}) as one of
    `user_id`'s own; returns its id. The same id and name updates that scenario; a new name -- or an id
    that isn't theirs, such as a shared scenario they started from -- makes a new one. Names are unique
    per player. Only a New Scenario's settings can be saved (GC72's can't: it has personal settings)."""
    try:
        name, description, settings = setups.checked(scenario)
    except LobbyError as e:
        raise StoreError(e.problems) from None
    with store.transaction():
        editing = store.fetch('scenarios', id=scenario.get('id')) if scenario.get('id') else None
        doc = {'name': name, 'description': description, 'generator_id': GENERATOR_ID, 'settings': settings,
               'saved': utc_now()}
        if editing is not None and editing['owner_id'] == user_id and editing['name'] == name:
            sid = editing['id']
            store.update('scenarios', {**editing, **doc}, {'id': sid}, name=name)
        else:
            sid = store.next_id('scenario')
            store.insert('scenarios', {'id': sid, 'owner_id': user_id, **doc, 'created': doc['saved']},
                         id=sid, owner_id=user_id, name=name)
    return sid


def delete_own(store, user_id, scenario_id):
    """Deletes one of `user_id`'s own scenarios; anyone else's (or none) is refused."""
    doc = store.fetch('scenarios', id=scenario_id)
    if doc is None:
        raise StoreError(f'there is no scenario {scenario_id!r}')
    if doc['owner_id'] != user_id:
        raise StoreError('you can only delete your own scenarios')
    store.delete('scenarios', id=scenario_id)


def get_own(store, scenario_id):
    """An own scenario's document, whoever owns it (a game lobby shows its settings to everyone), or None."""
    return store.fetch('scenarios', id=scenario_id)


# ---- shared scenarios: admins only -------------------------------------------------------------------

def save_shared(store, user_id, setup, repo=None):
    """An admin saving a shared scenario (setups.save: same name updates, new name adds) -- or, `setup`'s id
    'fixed', GC72's default settings (setups.save_fixed); returns its id."""
    _admin(store, user_id)
    try:
        if setup.get('id') == 'fixed':
            setups.save_fixed(setup.get('settings'), repo)
            return 'fixed'
        return setups.save(setup, repo)
    except LobbyError as e:
        raise StoreError(e.problems) from None


def delete_shared(store, user_id, setup_id, repo=None):
    """An admin deleting a shared scenario; everyone's personal settings for it go with it."""
    _admin(store, user_id)
    try:
        setups.delete(setup_id, repo)
    except LobbyError as e:
        raise StoreError(e.problems) from None
    store.delete('user_settings', scenario_id=setup_id)


def _admin(store, user_id):
    if not accounts.is_admin(store, user_id):
        raise StoreError('only an admin can change the shared scenarios')
