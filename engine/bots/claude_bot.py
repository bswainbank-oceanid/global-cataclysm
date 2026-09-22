"""
A bot seat played by Claude itself: each phase, it's handed a compact description of the board (its
own territories in full detail, the "frontier" of enemy/foreign territory next to its own units, and
a per-faction overview -- no fog of war, matching what a human player would see, and NOTHING about any
other seat's bot configuration), plus a battle_sim.estimate tool it can call any number of times before
committing to a final decision. It never sees another BOT's alliance_strategy/alliance_behavior/style --
only the same public game state a human client would.

Subclasses RandomBot and overrides ONLY the four plan_*_phase methods (Purchase/Combat Move/Non-Combat
Move/Diplomacy) -- everything else (take_*_phase, commit_diplomacy_phase, the alliance/treacherous-
reroll hooks, _garrisons_an_sc) is inherited unchanged, so server/stepper.py's generic bot.plan_*/
bot.commit_diplomacy_phase dispatch (see its own _apply_diplomacy) needs no ClaudeBot-specific code at
all -- this slots in exactly where RandomBot/StrategyBot already do.

Requires the `anthropic` package and an ANTHROPIC_API_KEY in the environment (checked eagerly at
construction time, not lazily on first use -- so server/lobby.py's build_session fails clearly when a
game is started with a Claude seat and no key, rather than mysteriously erroring mid-game on that
seat's first turn). Every call is synchronous/blocking -- this project runs one game at a time (see
server/app.py's own docstring), so a Claude seat's turn simply pauses that one game the same way an
opponent's turn already can (Settings' pause options), rather than needing the server's asyncio loop
threaded through the engine's call stack for one bot type.

Real cost and real (non-deterministic, not reproducible via --seed) API calls: unlike every other bot,
this one can't be used in the automated test suite or bulk simulation runs (tools/bot_arena.py and
friends) -- see engine/tests/test_claude_bot.py, which injects a fake `client` instead of calling the
real API at all.
"""
import json
import os

from ..engine import CombatMoveOrder, NonCombatMoveOrder, PurchaseOrder
from ..state import FactionMode
from . import battle_sim
from .random_bot import RandomBot

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
MAX_TOOL_ROUNDS = 6  # bounded so a confused/looping model can never hang the game
MAX_TOKENS = 1024

_SYSTEM_PREAMBLE = (
    "You are playing Global Cataclysm: 1972, an alternate-history strategy wargame (territory control, "
    "dice-based combat, a shared economy, alliances up to {max_alliance_size} members) as faction {faction}. "
    "You are given the full public board state (nobody has fog of war) but nothing about any other "
    "seat's own strategy or personality -- decide purely from what you can see. Every unit id and "
    "territory id below is real and must be used exactly as given."
)


class ClaudeBot(RandomBot):
    def __init__(self, engine, faction, rng=None, model=DEFAULT_MODEL, client=None, max_tool_rounds=MAX_TOOL_ROUNDS):
        super().__init__(engine, faction, rng=rng)
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.client = client or _default_client()

    # ---- board/context (kept deliberately compact -- the real map has 149 territories; dumping all
    # of them every call would burn a huge amount of context for no benefit) ------------------------

    def _faction_overview(self):
        gs = self.engine.game_state
        terrs = self.engine.data.territories()
        overview = []
        for code, f in gs.factions.items():
            if f.mode not in (FactionMode.HUMAN, FactionMode.BOT):
                continue
            terr_count = sum(1 for t in gs.territories.values() if t.owner == code and terrs[t.territory_id]['type'] == 'land')
            sc_count = sum(1 for tid, t in gs.territories.items()
                            if t.owner == code and terrs[tid]['type'] == 'land' and gs.is_strategic_center(tid, terrs[tid]))
            overview.append({'faction': code, 'territories': terr_count, 'strategic_centers': sc_count,
                              'treasury_mpc': f.treasury_mpc, 'alliance': f.alliance, 'eliminated': f.eliminated})
        return overview

    @staticmethod
    def _unit_counts(units):
        out = {}
        for u in units:
            key = f"{u.unit_type}{'*P' + str(u.promotions) if u.promotions else ''}"
            out.setdefault(u.owner, {}).setdefault(key, 0)
            out[u.owner][key] += 1
        return out

    def _terr_brief(self, tid, t, info):
        gs = self.engine.game_state
        return {
            'id': tid, 'name': info.get('name', str(tid)), 'type': info['type'], 'value': info.get('value'),
            'strategic_center': gs.is_strategic_center(tid, info), 'owner': t.owner,
            'contested_by': sorted(t.contested_by) if t.contested_by else None,
            'units_by_owner': self._unit_counts(t.units),
        }

    def _board_context(self):
        """faction_overview + my own territories (full unit detail, with real ids) + every currently-
        contested territory + the 'frontier' -- every territory NOT mine adjacent to one of my own
        units (everyone else is out of reach this turn regardless)."""
        gs = self.engine.game_state
        terrs = self.engine.data.territories()
        adjacency = self.engine.data.adjacency()

        my_territories = []
        my_owned_ids = set()
        frontier_ids = set()
        for tid, t in sorted(gs.territories.items()):
            info = terrs[tid]
            has_my_units = any(u.owner == self.faction for u in t.units)
            if t.owner == self.faction or has_my_units:
                my_owned_ids.add(tid)
                entry = self._terr_brief(tid, t, info)
                entry['my_units'] = [{'id': u.unit_id, 'type': u.unit_type, 'hp': u.current_hp,
                                       'promotions': u.promotions, 'moved_combat': u.has_moved_combat,
                                       'moved_noncombat': u.has_moved_noncombat}
                                      for u in t.units if u.owner == self.faction]
                my_territories.append(entry)
                for n in adjacency.get(tid, []):
                    if gs.territories[n].owner != self.faction:
                        frontier_ids.add(n)

        contested = [self._terr_brief(tid, t, terrs[tid]) for tid, t in gs.territories.items()
                     if t.contested_by and tid not in my_owned_ids]
        frontier = [self._terr_brief(tid, gs.territories[tid], terrs[tid])
                    for tid in sorted(frontier_ids - my_owned_ids) if not gs.territories[tid].contested_by]

        return {
            'round': gs.round_number, 'my_faction': self.faction,
            'faction_overview': self._faction_overview(),
            'my_territories': my_territories, 'contested_elsewhere': contested, 'frontier': frontier,
        }

    # ---- the one battle_sim tool, shared by every phase that offers it -----------------------------

    _BATTLE_SIM_TOOL = {
        'name': 'battle_sim_estimate',
        'description': ("Monte Carlo odds for a candidate attack: your own unit ids as attackers against "
                         "whichever units currently occupy `target`. Free to call any number of times before "
                         "deciding -- use it to size every attack you're actually considering; never guess."),
        'input_schema': {
            'type': 'object',
            'properties': {
                'unit_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'your own unit ids to send'},
                'target': {'type': 'integer', 'description': 'territory id to attack'},
            },
            'required': ['unit_ids', 'target'],
        },
    }

    def _tool_battle_sim_estimate(self, input_):
        gs = self.engine.game_state
        unit_ids = {int(i) for i in input_['unit_ids']}
        target = int(input_['target'])
        attackers = [u for t in gs.territories.values() for u in t.units if u.unit_id in unit_ids]
        defenders = list(gs.territories[target].units)
        terrs = self.engine.data.territories()
        battle_type = terrs[target]['type']
        # max_rounds=3: the real engine only fights 3 rounds of main combat (plus air superiority)
        # before calling a battle still contested -- omitting this would estimate odds for an
        # unbounded fight-to-the-death instead of what actually happens (found the hard way).
        side, _reason = self.engine.round1_bonus(self.faction, target, battle_type, attackers, defenders)
        odds = battle_sim.estimate(attackers, defenders, battle_type, self.engine.data.units(), self.engine.data.rules(),
                                    rng=self.rng, samples=300, round1_bonus_side=side, max_rounds=3)
        return {
            'attackers': [f'{u.unit_type}(id={u.unit_id},hp={u.current_hp})' for u in attackers],
            'defenders': [f'{u.owner}:{u.unit_type}(id={u.unit_id},hp={u.current_hp})' for u in defenders],
            'attacker_wins': round(odds.attacker_wins, 3), 'defender_wins': round(odds.defender_wins, 3),
            'contested_after_3_rounds': round(odds.contested, 3), 'mutual_destruction': round(odds.neither, 3),
        }

    # ---- the shared tool-use loop -------------------------------------------------------------------

    def _decide(self, system_prompt, extra_tools, submit_tool, initial_context, apply_fn, fallback_fn):
        """Runs the tool-use conversation until `submit_tool`'s call validates (apply_fn(decision)
        succeeds) or self.max_tool_rounds is used up. apply_fn must raise ValueError (or KeyError/
        TypeError, for a malformed decision) to ask Claude to try again -- the error is fed straight
        back as the next turn's context. Returns apply_fn's result on success, else fallback_fn()'s --
        fallback_fn must itself be a safe, always-legal action (e.g. submitting an empty order list),
        since it also covers a network/API failure, not just a stubborn model."""
        tools = list(extra_tools) + [submit_tool]
        submit_name = submit_tool['name']
        messages = [{'role': 'user', 'content': json.dumps(initial_context)}]
        for _ in range(self.max_tool_rounds):
            try:
                response = self.client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=system_prompt, messages=messages, tools=tools)
            except Exception:
                return fallback_fn()
            content = list(response.content)
            messages.append({'role': 'assistant', 'content': content})
            tool_uses = [b for b in content if getattr(b, 'type', None) == 'tool_use']
            if not tool_uses:
                messages.append({'role': 'user', 'content':
                                  f'Please decide by calling {submit_name} (use other tools first if that helps).'})
                continue
            tool_results = []
            decision = None
            for tu in tool_uses:
                if tu.name == submit_name:
                    decision = tu.input
                    tool_results.append({'type': 'tool_result', 'tool_use_id': tu.id, 'content': 'received'})
                else:
                    result = self._tool_battle_sim_estimate(tu.input) if tu.name == 'battle_sim_estimate' else {}
                    tool_results.append({'type': 'tool_result', 'tool_use_id': tu.id, 'content': json.dumps(result)})
            if decision is not None:
                try:
                    return apply_fn(decision)
                except (ValueError, KeyError, TypeError) as e:
                    tool_results.append({'type': 'text', 'text': f'That was rejected: {e}. Please try again.'})
            messages.append({'role': 'user', 'content': tool_results})
        return fallback_fn()

    # ---- Purchase -------------------------------------------------------------------------------

    def plan_purchase_phase(self):
        self._maybe_reroll_variable_alliance_settings()
        self._maybe_roll_treacherous_intent()
        context = dict(self._board_context(), purchase_options=self.engine.purchase_options(self.faction))
        system = _SYSTEM_PREAMBLE.format(faction=self.faction, max_alliance_size=self.engine.game_state.max_alliance_size) + (
            " It's your Purchase phase: spend (or save) your treasury on new units, deployed at any legal "
            "target in purchase_options.targets (remaining = how many more units that target can still take "
            "this turn; next_sc = whether the next unit bought there gets the cheaper Strategic Center price; "
            "sources = which of your territories the capacity/cost is drawn from). Purchased units do not "
            "exist on the board until next turn's Deploy + Income -- they cannot fight or move this turn.")
        submit_tool = {
            'name': 'submit_purchase',
            'description': 'Your final Purchase decision for this turn.',
            'input_schema': {'type': 'object', 'properties': {'orders': {'type': 'array', 'items': {
                'type': 'object', 'properties': {
                    'unit_type': {'type': 'string'}, 'qty': {'type': 'integer'}, 'deploy_at': {'type': 'integer'}},
                'required': ['unit_type', 'qty', 'deploy_at']}}}, 'required': ['orders']},
        }

        def apply(decision):
            orders = [PurchaseOrder(o['unit_type'], int(o['qty']), int(o['deploy_at'])) for o in decision.get('orders', [])]
            self.engine.submit_purchases(self.faction, orders)

        def fallback():
            self.engine.submit_purchases(self.faction, [])

        self._decide(system, [], submit_tool, context, apply, fallback)

    # ---- Combat Move ------------------------------------------------------------------------------

    def plan_combat_move_phase(self):
        context = dict(self._board_context(), legal_combat_move_options=self.engine.legal_combat_move_options(self.faction))
        system = _SYSTEM_PREAMBLE.format(faction=self.faction, max_alliance_size=self.engine.game_state.max_alliance_size) + (
            " It's your Combat Move phase: legal_combat_move_options lists, per unit id you still control, "
            "every {destination: path} it could attack into -- a combat move IS the attack, relocating the "
            "unit into the target and fighting there. Use battle_sim_estimate to size every attack you're "
            "actually considering before committing (never guess); a battle stalemated after 3 rounds stays "
            "contested, not lost. An empty orders list means no attacks this turn -- entirely legal.")
        submit_tool = {
            'name': 'submit_combat_moves',
            'description': 'Your final Combat Move decision for this turn.',
            'input_schema': {'type': 'object', 'properties': {'orders': {'type': 'array', 'items': {
                'type': 'object', 'properties': {
                    'unit_id': {'type': 'integer'}, 'path': {'type': 'array', 'items': {'type': 'integer'}}},
                'required': ['unit_id', 'path']}}}, 'required': ['orders']},
        }

        def apply(decision):
            orders = [CombatMoveOrder(int(o['unit_id']), [int(x) for x in o['path']]) for o in decision.get('orders', [])]
            self.engine.submit_combat_moves(self.faction, orders)

        def fallback():
            self.engine.submit_combat_moves(self.faction, [])

        self._decide(system, [self._BATTLE_SIM_TOOL], submit_tool, context, apply, fallback)

    # ---- Non-Combat Move ---------------------------------------------------------------------------

    def plan_noncombat_move_phase(self):
        if not self.engine.has_processed_return_to_base(self.faction):
            self.engine.process_return_to_base(self.faction)
        context = dict(self._board_context(),
                        legal_noncombat_move_options=self.engine.legal_noncombat_move_options(self.faction))
        system = _SYSTEM_PREAMBLE.format(faction=self.faction, max_alliance_size=self.engine.game_state.max_alliance_size) + (
            " It's your Non-Combat Move phase: legal_noncombat_move_options lists, per unit id that hasn't "
            "already combat-moved this turn, every destination it could reposition to (friendly territory, "
            "or reinforcing one of your own contested spaces). An empty orders list means nobody moves -- "
            "entirely legal, and often correct if your units are already well placed.")
        submit_tool = {
            'name': 'submit_noncombat_moves',
            'description': 'Your final Non-Combat Move decision for this turn.',
            'input_schema': {'type': 'object', 'properties': {'orders': {'type': 'array', 'items': {
                'type': 'object', 'properties': {
                    'unit_id': {'type': 'integer'}, 'destination': {'type': 'integer'}},
                'required': ['unit_id', 'destination']}}}, 'required': ['orders']},
        }

        def apply(decision):
            orders = [NonCombatMoveOrder(int(o['unit_id']), int(o['destination'])) for o in decision.get('orders', [])]
            self.engine.submit_noncombat_moves(self.faction, orders)

        def fallback():
            self.engine.submit_noncombat_moves(self.faction, [])

        self._decide(system, [], submit_tool, context, apply, fallback)

    # ---- Diplomacy ----------------------------------------------------------------------------------

    def plan_diplomacy_phase(self):
        """Returns the same plan dict shape RandomBot.commit_diplomacy_phase (inherited, unchanged)
        already knows how to execute -- {'action': 'none'|'invite'|'withdraw', 'target'?, 'demands': [...]}.
        Validated against legal_alliance_options/legal_surrender_targets here (raising ValueError feeds
        back into the SAME retry loop as an illegal Purchase/Combat/Non-Combat Move decision would) --
        this method only ever returns something commit_diplomacy_phase can safely execute."""
        gs = self.engine.game_state
        alliance_options = self.engine.legal_alliance_options(self.faction)
        surrender_targets = self.engine.legal_surrender_targets(self.faction)
        context = dict(self._board_context(), alliance_options=alliance_options, surrender_targets=surrender_targets)
        system = _SYSTEM_PREAMBLE.format(faction=self.faction, max_alliance_size=self.engine.game_state.max_alliance_size) + (
            " It's your Diplomacy phase, your last this turn: at most ONE alliance action (invite a target "
            "from alliance_options.eligible_invite_targets, withdraw if alliance_options.can_withdraw, or "
            "none), plus any number of surrender demands against targets in surrender_targets (each already "
            "meets a legal ground -- forcing one eliminates them at once, its units gone, its territory left "
            "as is). Allies don't fight each other; the game ends once every remaining faction shares one "
            "alliance. A bot invited always accepts; a human is asked and may decline.")
        submit_tool = {
            'name': 'submit_diplomacy',
            'description': 'Your final Diplomacy decision for this turn.',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'alliance_action': {'type': 'object', 'properties': {
                        'action': {'type': 'string', 'enum': ['none', 'invite', 'withdraw']},
                        'target': {'type': 'string'}}, 'required': ['action']},
                    'surrender_demands': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['alliance_action', 'surrender_demands'],
            },
        }
        eligible_invite = set(alliance_options['eligible_invite_targets'])
        eligible_surrender = {t['target'] for t in surrender_targets}
        currently_allied = gs.factions[self.faction].alliance is not None

        def apply(decision):
            aa = decision.get('alliance_action', {'action': 'none'})
            action = aa.get('action', 'none')
            plan = {'action': action, 'demands': []}
            if action == 'invite':
                target = aa.get('target')
                if target not in eligible_invite:
                    raise ValueError(f'{target!r} is not an eligible invite target right now')
                plan['target'] = target
                # Matches RandomBot/StrategyBot's own convention (inherited commit_diplomacy_phase):
                # a BOT target's accept is resolved right here, not left to alliance_policy.accepts_invite
                # (accepts=None) -- a bot never declines another bot's outgoing invite in this package. A
                # HUMAN target is left None; commit_diplomacy_phase (or, on the live server, stepper.py's
                # own human-invite handling) resolves that case appropriately.
                if gs.factions[target].mode == FactionMode.BOT:
                    plan['accepts'] = True
            elif action == 'withdraw':
                if not alliance_options['can_withdraw'] or not currently_allied:
                    raise ValueError('withdrawing is not legal right now')
            elif action != 'none':
                raise ValueError(f"alliance_action must be 'none', 'invite', or 'withdraw', not {action!r}")
            demands = list(decision.get('surrender_demands', []))
            bad = [t for t in demands if t not in eligible_surrender]
            if bad:
                raise ValueError(f'not legal surrender targets right now: {bad}')
            plan['demands'] = demands
            return plan

        def fallback():
            return {'action': 'none', 'demands': []}

        return self._decide(system, [], submit_tool, context, apply, fallback)


def _default_client():
    # Checked BEFORE importing anthropic (not just before constructing the client): fails clearly on
    # the missing key alone, without also depending on the (heavier, network-related) import succeeding.
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise RuntimeError(
            "ClaudeBot needs ANTHROPIC_API_KEY set in the server's environment (checked here, at seat "
            "creation time, rather than failing confusingly mid-game on this seat's first turn)."
        )
    import anthropic
    return anthropic.Anthropic()
