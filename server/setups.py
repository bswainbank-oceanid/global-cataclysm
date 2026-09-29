"""
Saved scenario setups: the launch screen's New Scenario settings, saved under a name
so they can be picked again from the scenario list (not the maps they deal -- every
game started from one is dealt afresh). Each is a ScenarioSetup module,
data/modules/scenario_setup/<id>.json (docs/DATA_MODEL.md):

    {"module_type": "ScenarioSetup", "id": "Setup_001", "name": ..., "description": ...,
     "generator_id": "GC72_Generator", "settings": {...the launch settings, server/lobby.py...}}

Saving keeps the id of the setup being edited while its name is unchanged, and makes
a new setup (a new id) when the name is new; names are unique. The settings are
checked like a game start's, so every saved setup can be started as it is.
"""
from engine.repository import default_repository
from .lobby import GENERATOR_ID, LobbyError, check_settings

MODULE_TYPE = 'ScenarioSetup'
ID_PREFIX = 'Setup_'
MAX_NAME = 60
MAX_DESCRIPTION = 2000
# what a saved setup leaves out: a seed replays one game, and dev switches are for testing
NOT_SAVED = ('seed', 'dev')
# a seat's keys in the order they are stored (and are columns of sheets/ScenarioSetup.ods)
SEAT_KEYS = ('mode', 'faction', 'alliance', 'strategy', 'behavior', 'ai',
             'territory_value', 'initial_mpc', 'units_mpc', 'promotions', 'scs')


def canonical(settings):
    """`settings` with each seat's keys in SEAT_KEYS order (any others after) and whole numbers as integers
    (a client may send 2.0), so a saved setup reads the same whichever client wrote it and survives the
    spreadsheet round trip unchanged."""
    out = _whole(dict(settings))
    if isinstance(out.get('seats'), list):
        out['seats'] = [{**{k: seat[k] for k in SEAT_KEYS if k in seat}, **{k: v for k, v in seat.items() if k not in SEAT_KEYS}}
                        if isinstance(seat, dict) else seat for seat in out['seats']]
    return out


def _whole(v):
    if isinstance(v, dict):
        return {k: _whole(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_whole(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def listing(repo=None):
    """Every saved setup, by name: [{id, name, description, settings}]."""
    repo = repo or default_repository()
    docs = sorted(repo.all(MODULE_TYPE), key=lambda d: (d['name'].lower(), d['id']))
    return [{'id': d['id'], 'name': d['name'], 'description': d.get('description', ''), 'settings': d['settings']}
            for d in docs]


def _next_id(repo):
    used = [int(i[len(ID_PREFIX):]) for i in repo.ids(MODULE_TYPE)
            if i.startswith(ID_PREFIX) and i[len(ID_PREFIX):].isdigit()]
    return f'{ID_PREFIX}{max(used, default=0) + 1:03d}'


def save(setup, repo=None):
    """Saves `setup` ({id: the setup being edited or null, name, description, settings}); returns the id
    saved under. Raises LobbyError."""
    repo = repo or default_repository()
    name = str(setup.get('name') or '').strip()
    description = str(setup.get('description') or '').strip()
    settings = setup.get('settings')
    problems = []
    if not name:
        problems.append('a saved scenario needs a name')
    elif len(name) > MAX_NAME:
        problems.append(f'a scenario name may be at most {MAX_NAME} characters')
    if len(description) > MAX_DESCRIPTION:
        problems.append(f'a description may be at most {MAX_DESCRIPTION} characters')
    if not isinstance(settings, dict):
        problems.append('a saved scenario needs its settings')
    else:
        settings = canonical({k: v for k, v in settings.items() if k not in NOT_SAVED})
        if (settings.get('scenario') or {}).get('kind') != 'new':
            problems.append('only a new scenario\'s settings can be saved')
        else:
            problems += check_settings(settings)
    if problems:
        raise LobbyError(problems)

    editing = setup.get('id')
    current = {d['id']: d for d in repo.all(MODULE_TYPE)}
    if editing in current and current[editing]['name'] == name:
        module_id = editing  # the name is unchanged: update it
    else:
        clash = [d['id'] for d in current.values() if d['name'].lower() == name.lower()]
        if clash:
            raise LobbyError([f'a saved scenario is already called {name!r}: choose another name'])
        module_id = _next_id(repo)
    repo.save({'module_type': MODULE_TYPE, 'id': module_id, 'name': name, 'description': description,
               'generator_id': GENERATOR_ID, 'settings': settings})
    return module_id


def delete(module_id, repo=None):
    repo = repo or default_repository()
    if module_id not in repo.ids(MODULE_TYPE):
        raise LobbyError([f'there is no saved scenario {module_id!r}'])
    repo.delete(MODULE_TYPE, module_id)
