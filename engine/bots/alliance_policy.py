"""
Bot decision-making for the Alliances phase -- WHEN a bot invites, accepts,
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

Four strategies (game_start setting, per bot, fixed for the whole game):
- aggressive: always invites a random eligible faction (capped by
  max_alliance_size), and always accepts an invitation -- wants the
  largest possible alliance, confirmed this session to grow via either path.
- passive: never invites; always accepts.
- counterweight: invites only to match the CURRENT LARGEST other alliance
  on the board, never inviting past that size, and does nothing at all
  while no other alliance yet exists (confirmed: it doesn't found the
  first alliance). Applies that same "never end up bigger than the
  largest other alliance" cap to ACCEPTING an invitation too, confirmed
  this session -- not just to its own inviting.
- independent: never invites, never accepts.

Three behaviors (game_start setting, per bot, fixed for the whole game;
irrelevant when game_start_settings.can_withdraw_from_alliances is False,
since withdrawing is simply never possible then):
- loyal: never withdraws, no exceptions.
- opportunistic: withdraws if it's now MUCH stronger than its weakest ally
  (>150% of either current treasury or total unit value) or MUCH weaker
  than its strongest ally (<50% of either metric) -- confirmed this
  session: "MPC" means FactionState.treasury_mpc (current balance, not
  cumulative), and unit value only ever needs counting deployed units
  since the Alliances phase runs after Deploy + Income. ALSO always
  withdraws if the game would otherwise end on this faction's own turn
  (victory.game_end_rule via GameEngine.would_game_end()) -- confirmed
  this session, and confirmed to apply to treacherous too.
- treacherous: a 15% chance, rolled once at the START of each of its own
  turns (RandomBot.take_purchase_phase, while it's still in an alliance),
  to withdraw at that turn's Alliances phase regardless of any other
  factor -- see FactionState.pending_treacherous_withdrawal. ALSO always
  withdraws if the game would otherwise end on this faction's own turn,
  same override as opportunistic (confirmed this session).
"""
STRATEGIES = ('aggressive', 'passive', 'counterweight', 'independent')
BEHAVIORS = ('loyal', 'opportunistic', 'treacherous')


def resolve_alliance_strategy(value, rng):
    """`value`: one of STRATEGIES, 'random' (case-insensitive), or None
    (treated as 'random'). Rolls 'random' to a concrete STRATEGIES member
    ONCE here -- the caller (setup.build_game_state) stores only the
    resolved result on FactionState.alliance_strategy, never 'random'
    itself, so it stays fixed for the rest of the game."""
    value = (value or 'random').lower()
    return rng.choice(STRATEGIES) if value == 'random' else value


def resolve_alliance_behavior(value, rng):
    """Same resolution as resolve_alliance_strategy, for BEHAVIORS."""
    value = (value or 'random').lower()
    return rng.choice(BEHAVIORS) if value == 'random' else value


def _eligible_invite_targets(engine, faction):
    """Every active faction `faction` could legally invite right now --
    not already one of its own allies, not already in SOME alliance
    (must withdraw first), and, when can_rejoin_alliances is False, not
    former_allies-banned against anyone already in faction's alliance.
    A cheap pre-filter; GameEngine.invite_to_alliance is still the
    authoritative check."""
    gs = engine.game_state
    members = engine._alliance_members(faction)
    candidates = []
    for code in gs.active_factions():
        if code in members:
            continue
        if gs.factions[code].alliance is not None:
            continue
        if not gs.can_rejoin_alliances and gs.factions[code].former_allies & members:
            continue
        candidates.append(code)
    return candidates


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


def choose_invite_target(engine, faction, rng):
    """Which faction (if any) `faction`'s alliance_strategy wants to
    invite this Alliances phase -- None means do nothing. Purely
    advisory: the caller must still call GameEngine.invite_to_alliance,
    which re-validates everything authoritatively (this function's own
    checks exist only to avoid the common-case wasted attempt)."""
    gs = engine.game_state
    strategy = gs.factions[faction].alliance_strategy
    if strategy not in ('aggressive', 'counterweight'):
        return None

    own_size = len(engine._alliance_members(faction))
    if own_size + 1 > gs.max_alliance_size:
        return None

    if strategy == 'counterweight':
        others = _other_alliance_sizes(engine, gs.factions[faction].alliance)
        if not others or own_size >= max(others):
            return None

    candidates = _eligible_invite_targets(engine, faction)
    if not candidates:
        return None
    return rng.choice(candidates)


def accepts_invite(engine, faction, inviter):
    """Whether `faction` (the invitee) accepts an invitation from
    `inviter` right now, per faction's OWN alliance_strategy -- not the
    inviter's. Independent never accepts; Aggressive/Passive always do
    (confirmed this session); Counterweight applies its size cap to
    accepting too, comparing the prospective merged alliance's size
    against the largest alliance other than the one it would be joining."""
    gs = engine.game_state
    strategy = gs.factions[faction].alliance_strategy
    if strategy not in STRATEGIES:
        return False
    if strategy == 'independent':
        return False
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
    """Whether `faction`'s alliance_behavior wants to withdraw this
    Alliances phase. Caller must still confirm `faction` is actually in
    an alliance and that can_withdraw_from_alliances is True -- this
    function only decides WANTS, never checks legality."""
    gs = engine.game_state
    behavior = gs.factions[faction].alliance_behavior
    if behavior not in BEHAVIORS:
        return False
    if behavior == 'loyal':
        return False
    if behavior in ('opportunistic', 'treacherous') and engine.would_game_end():
        return True
    if behavior == 'opportunistic':
        return _opportunistic_strength_mismatch(engine, faction)

    fstate = gs.factions[faction]
    intent = fstate.pending_treacherous_withdrawal
    fstate.pending_treacherous_withdrawal = False
    return intent
