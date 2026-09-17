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

"Rounds contested" (per capture) counts how many separate times combat
was RESOLVED for that territory (GameEngine.resolve_combat's per-battle
loop, win, loss, or 3-round stalemate alike) while it stayed
continuously contested, right up to the capture -- not the up-to-3
sub-battle dice-rolling rounds within any one of those resolutions.
Reset to 0 whenever a territory stops being contested for any other
reason too (e.g. the attacking coalition losing all presence -- see
combat.contested_territory_rule's claiming_update), so a later, separate
contest over the same ground starts counting fresh.

"Cumulative MPC" is the running total of income actually COLLECTED
during play (every Deploy + Income phase, GameEngine.deploy_and_collect_
income) -- it does NOT include a faction's one-time starting treasury
from game setup, since GameStats has no visibility into that (it's only
ever attached to a GameEngine, constructed after setup.build_game_state
has already run). "Final MPC" is simply GameState.factions[code].
treasury_mpc read at report time, i.e. whatever's left after all that
income and all Purchase-phase spending -- report() takes an optional
`game_state` to read it from; omit it and the Treasury section is left
out entirely.
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
    cumulative_mpc: dict = field(default_factory=dict)     # faction -> total income ever collected
    _contest_rounds: dict = field(default_factory=dict)    # territory_id -> battle-resolution count since last uncontested (internal bookkeeping, not reported directly)

    def record_capture(self, turn, faction, territory_id, previous_owner):
        rounds_contested = self._contest_rounds.pop(territory_id, 0)
        self.captures.append({
            'turn': turn, 'faction': faction, 'territory_id': territory_id,
            'previous_owner': previous_owner, 'rounds_contested': rounds_contested,
        })

    def record_battle_resolved(self, territory_id):
        self._contest_rounds[territory_id] = self._contest_rounds.get(territory_id, 0) + 1

    def record_contest_ended_without_capture(self, territory_id):
        self._contest_rounds.pop(territory_id, None)

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

    def record_income(self, faction, amount):
        self.cumulative_mpc[faction] = self.cumulative_mpc.get(faction, 0) + amount

    def report(self, game_state=None):
        """A plain-text summary: the capture log in turn order (each
        line noting how many rounds that territory was contested before
        it changed hands), then one line per (faction, unit_type) that
        appears in any of the four unit counters, then -- only if
        `game_state` is given -- a Treasury section with each faction's
        cumulative and final MPC."""
        lines = ['=== Territory Captures ===']
        if self.captures:
            for c in self.captures:
                prev = c['previous_owner'] or 'unowned'
                lines.append(
                    f"Turn {c['turn']}: {c['faction']} captured territory {c['territory_id']} "
                    f"(from {prev}, contested for {c['rounds_contested']} round(s))"
                )
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

        if game_state is not None:
            lines.append('')
            lines.append('=== Treasury ===')
            for code in game_state.factions:
                final_mpc = game_state.factions[code].treasury_mpc
                cumulative = self.cumulative_mpc.get(code, 0)
                lines.append(f'{code}: final MPC={final_mpc} cumulative MPC={cumulative}')

        return '\n'.join(lines)
