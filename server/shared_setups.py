"""
The shared scenarios (an admin's Scenarios mode: server/setups.py's ScenarioSetup documents, GC72's own
default settings among them) kept in the player database, not in the code's data/modules/scenario_setup:
a server's admins edit them, and updating the code from git must never touch, or be blocked by, their
edits. Backed up with the rest of the database (tools/backup_db.py).

    repo = SharedSetups(store, seed=default_repository())
    setups.listing(repo); setups.save(setup, repo)            # (server/setups.py, as with files)

Stands in for a module repository (all / ids / get / save / delete) for ScenarioSetup documents; any other
module type is read from `seed`, unchanged. The first time a database is used this way, it takes `seed`'s
ScenarioSetup files as its starting set (once: a database whose admins have since deleted them all stays
empty).
"""
from engine.repository import ModuleNotFound

from .setups import MODULE_TYPE
from .store import utc_now

SEEDED = 'shared_scenarios_seeded'  # server_settings key: when the starting set was copied in


class SharedSetups:
    def __init__(self, store, seed):
        self.store = store
        self.seed = seed
        self._seed_once()

    def _seed_once(self):
        if self.store.fetch('server_settings', key=SEEDED) is not None:
            return
        with self.store.transaction():
            for doc in self.seed.all(MODULE_TYPE):
                self.store.upsert('shared_scenarios', doc, id=doc['id'])
            self.store.upsert('server_settings', {'key': SEEDED, 'value': utc_now()}, key=SEEDED)

    # ---- the module repository's interface (server/setups.py uses these) --------------------------------

    def ids(self, module_type):
        if module_type != MODULE_TYPE:
            return self.seed.ids(module_type)
        return sorted(d['id'] for d in self.store.fetch_all('shared_scenarios'))

    def all(self, module_type):
        if module_type != MODULE_TYPE:
            return self.seed.all(module_type)
        return sorted(self.store.fetch_all('shared_scenarios'), key=lambda d: d['id'])

    def get(self, module_type, module_id):
        if module_type != MODULE_TYPE:
            return self.seed.get(module_type, module_id)
        doc = self.store.fetch('shared_scenarios', id=module_id)
        if doc is None:
            raise ModuleNotFound(f'no {module_type} {module_id!r} in the shared scenarios')
        return doc

    def save(self, doc):
        if doc.get('module_type') != MODULE_TYPE:
            raise TypeError(f"only {MODULE_TYPE} documents are kept here, not {doc.get('module_type')!r}")
        self.store.upsert('shared_scenarios', doc, id=doc['id'])

    def delete(self, module_type, module_id):
        if module_type == MODULE_TYPE:
            self.store.delete('shared_scenarios', id=module_id)
