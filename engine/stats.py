"""
Turn-by-turn and end-of-game statistics for a bot-driven (or any) game.
GameStats is purely an observer: GameEngine, when constructed with a
GameStats instance (the `stats` kwarg), reports into it at the points
where each fact first becomes known -- a unit lands on the board, a
capture is awarded, a battle event resolves -- but nothing here drives
or affects the game itself; a GameEngine with no stats sink behaves
identically to before this module existed.

"Deployed" is counted when a purchased unit actually lands on the board
(engine.GameEngine._deploy_to_land/_deploy_to_sea), not when it's bought
-- a purchase that's lost outright (no fallback territory available)
never counts. "Deaths in transport form" is the subset of a LAND unit's
deaths that happened while it was physically occupying a sea territory
(engine.combat's battle_type == 'sea') -- there's no separate Transport
UnitInstance to check (see movement.py's module docstring); the land
unit's own current location at the moment of death is the signal.
"""
from dataclasses import dataclass, field


@dataclass
class GameStats:
    captures: list = field(default_factory=list)
    deployed: dict = field(default_factory=dict)          # (faction, unit_type) -> count
    promotions: dict = field(default_factory=dict)         # (faction, unit_type) -> count
    deaths: dict = field(default_factory=dict)             # (faction, unit_type) -> count
    transport_deaths: dict = field(default_factory=dict)   # (faction, unit_type) -> count, subset of deaths
    kills: dict = field(default_factory=dict)              # (faction, unit_type) -> count

    def record_capture(self, turn, faction, territory_id, previous_owner):
        self.captures.append({
            'turn': turn, 'faction': faction, 'territory_id': territory_id, 'previous_owner': previous_owner,
        })

    def record_deploy(self, faction, unit_type, qty=1):
        key = (faction, unit_type)
        self.deployed[key] = self.deployed.get(key, 0) + qty

    def record_promotion(self, faction, unit_type):
        key = (faction, unit_type)
        self.promotions[key] = self.promotions.get(key, 0) + 1

    def record_death(self, faction, unit_type, in_transport=False):
        key = (faction, unit_type)
        self.deaths[key] = self.deaths.get(key, 0) + 1
        if in_transport:
            self.transport_deaths[key] = self.transport_deaths.get(key, 0) + 1

    def record_kill(self, faction, unit_type):
        key = (faction, unit_type)
        self.kills[key] = self.kills.get(key, 0) + 1

    def report(self):
        """A plain-text summary: the capture log in turn order, then one
        line per (faction, unit_type) that appears in any of the four
        counters, with the transport-death count called out alongside
        ordinary deaths when it's nonzero."""
        lines = ['=== Territory Captures ===']
        if self.captures:
            for c in self.captures:
                prev = c['previous_owner'] or 'unowned'
                lines.append(f"Turn {c['turn']}: {c['faction']} captured territory {c['territory_id']} (from {prev})")
        else:
            lines.append('(none)')

        lines.append('')
        lines.append('=== Unit Stats ===')
        keys = sorted(set(self.deployed) | set(self.promotions) | set(self.deaths) | set(self.kills))
        for faction, unit_type in keys:
            deployed = self.deployed.get((faction, unit_type), 0)
            promoted = self.promotions.get((faction, unit_type), 0)
            died = self.deaths.get((faction, unit_type), 0)
            killed = self.kills.get((faction, unit_type), 0)
            transport_died = self.transport_deaths.get((faction, unit_type), 0)
            line = f'{faction} {unit_type}: deployed={deployed} promotions={promoted} deaths={died} kills={killed}'
            if transport_died:
                line += f' (deaths in transport form={transport_died})'
            lines.append(line)
        if not keys:
            lines.append('(none)')

        return '\n'.join(lines)
