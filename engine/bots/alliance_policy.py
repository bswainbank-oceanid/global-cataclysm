"""
Bot decision-making for the Diplomacy phase -- WHEN a bot invites, accepts,
or withdraws, layered on top of the engine's Alliance System mechanics
(engine.engine.GameEngine.invite_to_alliance/withdraw_from_alliance), which
only enforce validity, not strategy. Deliberately separate from engine.py:
"first the engine" was built without any bot policy at all (an earlier
session), and this module is the "now the bots" follow-up.

Every function here is a pure query against (engine, faction) plus
FactionState.alliance_strategy/alliance_behavior -- resolved once, at game
start, from a per-bot game-start setting (see resolve_alliance_strategy/
resolve_alliance_behavior and setup.build_game_state's alliance_strategies/
alliance_behaviors params). Reading the decision straight off GameState
(rather than off a RandomBot instance) is what lets accepts_invite work for
ANY invited faction, not just the one instance currently taking its turn --
the accepting decision belongs to the TARGET's own strategy, not the
inviter's.

Six strategies (game_start setting, per bot):
- Invitations (aggressive and counterweight) rotate through the legal targets: a
  bot asks whoever it asked least recently (never-asked first), so a faction that
  declines waits until every other legal target has been asked, and one that has
  declined MAX_INVITE_DECLINES (5) times is never asked again (see _pick_target;
  the history lives on FactionState.last_invited/invites_declined).
- aggressive: always invites a random eligible faction (capped by
  GameEngine._effective_max_alliance_size() -- game_start_settings.
  max_alliance_size, further capped by the CURRENT number of active
  factions, so this shrinks as factions are eliminated, confirmed this
  session), and always accepts an invitation -- wants the largest
  possible alliance, confirmed this session to grow via either path.
- passive: never invites; always accepts.
- counterweight: invites only to match the CURRENT LARGEST other alliance
  on the board, never inviting past that size, and does nothing at all
  while no other alliance yet exists (confirmed: it doesn't found the
  first alliance). Applies that same "never end up bigger than the
  largest other alliance" cap to ACCEPTING an invitation too, confirmed
  this session -- not just to its own inviting.
- independent: never invites, never accepts.
- adversarial: never allies with a human. It never invites a human player or a
  faction in an alliance with one, and never invites anyone while its own
  alliance holds a human (which only a starting alliance can arrange); it never
  accepts an invitation from a human or from an alliance with one. It accepts
  every other bot's invitation, whatever its size, and invites other bots by
  the counterweight rule. Before forcing a faction to surrender it offers an
  alliance only where it would propose one anyway -- never to a human.
- underdog: allies only to stay the underdog -- by strength, a group's total income
  (territory MPC) and total unit value (UV), compared with the strongest other group on
  the board (each alliance one group, each unallied active player its own). It accepts an
  invitation only if the alliance it would form is under 130% of the strongest other
  group on BOTH counts, humans or bots. It invites (rotating, as above) only a faction whose joining keeps
  the alliance under 100% of the strongest other group on both counts, a human included.
  See _underdog_fits.
- variable (added this session): re-rolls to one of the four CONCRETE
  strategies above -- never variable or random themselves -- once at game
  start and again at the start of every one of this bot's own turns (see
  reroll_alliance_strategy/effective_alliance_strategy below), so which
  concrete strategy is actually driving its decisions can change turn to
  turn, but "variable" itself is fixed for the whole game exactly like
  every other choice (resolve_alliance_strategy still only resolves
  'random' ONCE, same as ever -- 'variable' is just one of the concrete
  outcomes that single resolution can now land on).

Four behaviors (game_start setting, per bot; irrelevant when
game_start_settings.can_withdraw_from_alliances is False, since
withdrawing is simply never possible then):
- loyal: never withdraws, no exceptions.
- opportunistic: withdraws if it's now MUCH stronger than its weakest ally
  (>150% of either current treasury or total unit value) or MUCH weaker
  than its strongest ally (<50% of either metric) -- confirmed this
  session: "MPC" means FactionState.treasury_mpc (current balance, not
  cumulative), and unit value only ever needs counting deployed units
  since the Diplomacy phase runs after Deploy + Income. ALSO always
  withdraws if the game would otherwise end on this faction's own turn
  (victory.game_end_rule via GameEngine.would_game_end()) -- confirmed
  this session, and confirmed to apply to treacherous too.
- treacherous: a 15% chance, rolled once at the START of each of its own
  turns (RandomBot.take_purchase_phase, while it's still in an alliance),
  to withdraw at that turn's Diplomacy phase regardless of any other
  factor -- see FactionState.pending_treacherous_withdrawal. ALSO always
  withdraws if the game would otherwise end on this faction's own turn,
  same override as opportunistic (confirmed this session).
- underdog: plans at the START of its own turn (like treacherous: the
  turn's Non-Combat Move then makes its treasonous captures) to withdraw at
  Diplomacy if its alliance has become the favourite -- its income OR unit
  value above 130% of the strongest other group's, humans or bots. Also always withdraws if the game would
  otherwise end on its own turn. See underdog_wants_out.
- variable (added this session): same re-roll-every-turn idea as the
  variable strategy above, over the three concrete behaviors -- a turn
  where its re-rolled pick happens to land on 'treacherous' rolls that
  behavior's own 15% chance too (see RandomBot._maybe_reroll_variable_
  alliance_settings/_maybe_roll_treacherous_intent ordering).

Every decision function below (choose_invite_target, accepts_invite,
should_withdraw) reads a faction's strategy/behavior through effective_
alliance_strategy/effective_alliance_behavior, never FactionState.
alliance_strategy/alliance_behavior directly -- that's the one place
'variable' is resolved to whatever concrete value is currently active,
so none of the actual decision logic needs to know 'variable' exists at
all.
"""
from ..state import FactionMode

# A bot stops inviting a faction once that faction has turned it down this many times.
MAX_INVITE_DECLINES = 5

STRATEGIES = ('aggressive', 'passive', 'counterweight', 'independent', 'adversarial', 'underdog', 'variable')
BEHAVIORS = ('loyal', 'opportunistic', 'treacherous', 'underdog', 'variable')

# Underdog: an alliance may be up to this much of the strongest other group's strength (income and unit
# value) when joining, and becomes the favourite -- time to leave -- above it, whether it holds a human or
# not. An invitation keeps the alliance under 1.0 of it.
UNDERDOG_MARGIN = 1.3

# What a 'variable' bot's own per-turn re-roll picks from (reroll_alliance_
# strategy/reroll_alliance_behavior) -- everything in STRATEGIES/BEHAVIORS
# except 'variable' itself; 'random' was never a real member of either
# tuple to begin with (see resolve_alliance_strategy/resolve_alliance_
# behavior). Also what the decision functions below check a resolved
# strategy/behavior against, so an unset (None, e.g. a HUMAN faction) or
# somehow-still-literally-'variable' value both correctly fail the check.
_CONCRETE_STRATEGIES = tuple(s for s in STRATEGIES if s != 'variable')
_CONCRETE_BEHAVIORS = tuple(b for b in BEHAVIORS if b != 'variable')


def resolve_alliance_strategy(value, rng):
    """`value`: one of STRATEGIES (now including 'variable'), 'random'
    (case-insensitive), or None (treated as 'random'). Rolls 'random' to
    a concrete STRATEGIES member ONCE here -- the caller (setup.
    build_game_state) stores only the resolved result on FactionState.
    alliance_strategy, never 'random' itself, so it stays fixed for the
    rest of the game -- 'random' rolling 'variable' is exactly as
    permanent a choice as rolling 'aggressive' would have been ("random
    selection can choose variable... for the entire game", this session);
    it's only the CONCRETE strategy actually driving decisions that then
    keeps changing turn to turn, via reroll_alliance_strategy, not this
    resolution happening again."""
    value = (value or 'random').lower()
    return rng.choice(STRATEGIES) if value == 'random' else value


def resolve_alliance_behavior(value, rng):
    """Same resolution as resolve_alliance_strategy, for BEHAVIORS."""
    value = (value or 'random').lower()
    return rng.choice(BEHAVIORS) if value == 'random' else value


def reroll_alliance_strategy(rng):
    """The concrete strategy a 'variable' bot re-rolls to -- at game
    start (setup.build_game_state, so it has a real decision from turn 1,
    not None) and again at the start of every one of its own turns
    (RandomBot._maybe_reroll_variable_alliance_settings) -- stored on
    FactionState.current_alliance_strategy, never on alliance_strategy
    itself (which stays 'variable' all game, same as any other resolved
    choice stays fixed). Never re-rolls to 'variable' or 'random'
    themselves."""
    return rng.choice(_CONCRETE_STRATEGIES)


def reroll_alliance_behavior(rng):
    """Same re-roll as reroll_alliance_strategy, for BEHAVIORS -- stored
    on FactionState.current_alliance_behavior."""
    return rng.choice(_CONCRETE_BEHAVIORS)


def effective_alliance_strategy(gs, faction):
    """The concrete strategy actually driving `faction`'s decisions right
    now: FactionState.alliance_strategy directly, UNLESS that's
    'variable', in which case FactionState.current_alliance_strategy (the
    latest re-roll) is what's actually consulted instead. The single
    place 'variable' is ever resolved -- every decision function in this
    module reads a strategy through here, never the raw field."""
    fstate = gs.factions[faction]
    if fstate.alliance_strategy == 'variable':
        return fstate.current_alliance_strategy
    return fstate.alliance_strategy


def effective_alliance_behavior(gs, faction):
    """Same resolution as effective_alliance_strategy, for BEHAVIORS."""
    fstate = gs.factions[faction]
    if fstate.alliance_behavior == 'variable':
        return fstate.current_alliance_behavior
    return fstate.alliance_behavior


def _eligible_invite_targets(engine, faction):
    """Every active faction `faction` could legally invite right now --
    delegates to GameEngine.legal_alliance_options (moved there this
    session, same reasoning as legal_purchase_targets' own earlier move:
    any caller, not just a bot, can use it now), which is still the
    authoritative pre-filter's source of truth; GameEngine.
    invite_to_alliance itself remains the final, real check."""
    return engine.legal_alliance_options(faction)['eligible_invite_targets']


def _alliance_tag_sizes(engine):
    gs = engine.game_state
    sizes = {}
    for code in gs.active_factions():
        tag = gs.factions[code].alliance
        if tag is None:
            continue
        sizes[tag] = sizes.get(tag, 0) + 1
    return sizes


def _other_alliance_sizes(engine, exclude_tag):
    """Sizes of every alliance currently on the board OTHER than
    `exclude_tag` -- an unallied faction (exclude_tag=None) isn't itself
    an alliance, so passing None simply means "every alliance counts."""
    return [size for tag, size in _alliance_tag_sizes(engine).items() if tag != exclude_tag]


def _alliance_has_human(engine, faction):
    """True if `faction` is a human player or allied with one."""
    gs = engine.game_state
    return any(gs.factions[m].mode == FactionMode.HUMAN for m in engine._alliance_members(faction))


def may_invite(engine, faction, target):
    """Whether `faction`'s (effective) alliance strategy lets it propose an alliance to `target` at all --
    the adversarial strategy never to a human or a faction allied with one, and never while its own
    alliance holds a human; the underdog strategy only when the alliance with `target` in it would still be
    weaker than the strongest other group (_underdog_fits, at 100%). (Used for the invitation a bot offers
    before forcing a surrender, as well as by choose_invite_target.)"""
    strategy = effective_alliance_strategy(engine.game_state, faction)
    if strategy == 'underdog':
        return _underdog_fits(engine, engine._alliance_members(faction) | engine._alliance_members(target), 1.0)
    if strategy != 'adversarial':
        return True
    return not _alliance_has_human(engine, faction) and not _alliance_has_human(engine, target)


# ---- underdog: strength by groups -------------------------------------------------------------------

def _group_strength(engine, members):
    """(total income, total unit value) of `members`."""
    from ..economy import compute_income
    gs = engine.game_state
    return (sum(compute_income(m, gs, engine.data) for m in members),
            sum(_total_unit_value(engine, m) for m in members))


def _strongest_other(engine, members):
    """(highest income, highest unit value) among the groups outside `members`: every other alliance as one
    group, every other unallied active player as its own -- each measure's own maximum. (0, 0) with nobody
    else left."""
    gs = engine.game_state
    groups = {}
    for code in gs.active_factions():
        if code in members:
            continue
        tag = gs.factions[code].alliance
        groups.setdefault(('alliance', tag) if tag is not None else ('alone', code), set()).add(code)
    strengths = [_group_strength(engine, g) for g in groups.values()]
    return (max((s[0] for s in strengths), default=0), max((s[1] for s in strengths), default=0))


def _underdog_fits(engine, members, margin):
    """An alliance of `members` stays the underdog: its income AND its unit value both under `margin` times
    the strongest other group's."""
    income, units = _group_strength(engine, members)
    top_income, top_units = _strongest_other(engine, members)
    return income < margin * top_income and units < margin * top_units


def underdog_wants_out(engine, faction):
    """The underdog behaviour's withdrawal plan, made at the start of `faction`'s turn: its alliance has
    become the favourite -- income OR unit value above 130% of the strongest other group's."""
    members = engine._alliance_members(faction)
    if len(members) < 2:
        return False
    margin = UNDERDOG_MARGIN
    income, units = _group_strength(engine, members)
    top_income, top_units = _strongest_other(engine, members)
    return income > margin * top_income or units > margin * top_units


def choose_invite_target(engine, faction, rng):
    """Which faction (if any) `faction`'s (effective) alliance_strategy
    wants to invite this Diplomacy phase -- None means do nothing. Purely
    advisory: the caller must still call GameEngine.invite_to_alliance,
    which re-validates everything authoritatively (this function's own
    checks exist only to avoid the common-case wasted attempt)."""
    gs = engine.game_state
    strategy = effective_alliance_strategy(gs, faction)
    if strategy not in ('aggressive', 'counterweight', 'adversarial', 'underdog'):
        return None

    own_size = len(engine._alliance_members(faction))
    if own_size + 1 > engine._effective_max_alliance_size():
        return None

    if strategy in ('counterweight', 'adversarial'):
        others = _other_alliance_sizes(engine, gs.factions[faction].alliance)
        if not others or own_size >= max(others):
            return None

    candidates = [t for t in _eligible_invite_targets(engine, faction) if may_invite(engine, faction, t)]
    return _pick_target(gs.factions[faction], candidates, rng)


def _pick_target(fstate, candidates, rng):
    """Whom to ask among the legal `candidates`: never a faction that has already
    declined MAX_INVITE_DECLINES times, and of the rest whoever was asked LEAST
    recently (never-asked first, ties at random) -- so once someone declines, every
    other legal target is asked before that faction is asked again."""
    candidates = [c for c in candidates if fstate.invites_declined.get(c, 0) < MAX_INVITE_DECLINES]
    if not candidates:
        return None
    oldest = min(fstate.last_invited.get(c, 0) for c in candidates)
    return rng.choice([c for c in candidates if fstate.last_invited.get(c, 0) == oldest])


def accepts_invite(engine, faction, inviter):
    """Whether `faction` (the invitee) accepts an invitation from
    `inviter` right now, per faction's OWN (effective) alliance_strategy
    -- not the inviter's. Independent never accepts; Aggressive/Passive
    always do (confirmed this session); Counterweight applies its size
    cap to accepting too, comparing the prospective merged alliance's
    size against the largest alliance other than the one it would be
    joining. Adversarial accepts any bot's invitation, never one from a human or from an alliance
    that holds one."""
    gs = engine.game_state
    strategy = effective_alliance_strategy(gs, faction)
    if strategy not in _CONCRETE_STRATEGIES:
        return False
    if strategy == 'independent':
        return False
    if strategy == 'adversarial':
        return not _alliance_has_human(engine, inviter)
    if strategy == 'underdog':
        joined = engine._alliance_members(inviter) | engine._alliance_members(faction)
        return _underdog_fits(engine, joined, UNDERDOG_MARGIN)
    if strategy in ('aggressive', 'passive'):
        return True

    prospective_size = len(engine._alliance_members(inviter)) + 1
    others = _other_alliance_sizes(engine, gs.factions[inviter].alliance)
    if not others:
        return True
    return prospective_size <= max(others)


def _total_unit_value(engine, faction):
    unit_defs = engine.data.units()
    total = 0
    for t in engine.game_state.territories.values():
        for u in t.units:
            if u.owner == faction:
                total += unit_defs[u.unit_type].get('cost') or 0
    return total


def _opportunistic_strength_mismatch(engine, faction):
    gs = engine.game_state
    members = engine._alliance_members(faction) - {faction}
    if not members:
        return False
    my_treasury = gs.factions[faction].treasury_mpc
    my_units = _total_unit_value(engine, faction)
    ally_treasuries = [gs.factions[m].treasury_mpc for m in members]
    ally_units = [_total_unit_value(engine, m) for m in members]

    return (
        my_treasury > 1.5 * min(ally_treasuries)
        or my_units > 1.5 * min(ally_units)
        or my_treasury < 0.5 * max(ally_treasuries)
        or my_units < 0.5 * max(ally_units)
    )


def should_withdraw(engine, faction):
    """Whether `faction`'s (effective) alliance_behavior wants to
    withdraw this Diplomacy phase. Caller must still confirm `faction` is
    actually in an alliance and that can_withdraw_from_alliances is True
    -- this function only decides WANTS, never checks legality."""
    gs = engine.game_state
    behavior = effective_alliance_behavior(gs, faction)
    if behavior not in _CONCRETE_BEHAVIORS:
        return False
    if behavior == 'loyal':
        return False
    if behavior in ('opportunistic', 'treacherous', 'underdog') and engine.would_game_end():
        return True
    if behavior == 'opportunistic':
        return _opportunistic_strength_mismatch(engine, faction)

    fstate = gs.factions[faction]
    intent = fstate.pending_treacherous_withdrawal
    fstate.pending_treacherous_withdrawal = False
    return intent
