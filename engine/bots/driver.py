"""
Drives a bot-controlled game to completion (or a safety-capped number of
turns) using GameEngine's real public phase API -- the same sequence a
human-driven UI would follow, just with a RandomBot supplying every
order. Not itself part of the rules engine; a convenience for
integration testing and for producing a stats.GameStats report of a
full game.
"""
from ..state import Phase


def play_to_completion(engine, bots, max_turns=500):
    """bots: {faction_code: RandomBot}, one entry per HUMAN/BOT faction
    in play. Drives full turns (Purchase through Alliances, per
    turn_order) for whichever faction is GameState.active_faction,
    calling advance_phase() between phases and advance_turn() at the end
    of each faction's turn, until GameState.game_over or `max_turns`
    full turns have been played -- a safety cap, since two bots with no
    lookahead can in principle stalemate forever; this just stops the
    loop rather than hanging. Returns the number of turns actually
    played.

    Phase-aware, not a fixed 7-call sequence: game_start_settings can
    make advance_phase() skip Combat Move and/or Non-Combat Move
    entirely for a faction's own first turn (GameState.phase never
    becomes that phase at all that turn). This is a single while loop
    over "whatever GameState.phase currently is" -- calling
    advance_phase() exactly once per phase actually encountered -- NOT
    one independent `if phase == X: ...; advance_phase()` block per
    phase in a fixed row. That fixed-block shape looks equivalent but
    isn't: advance_phase() already skips a disabled phase internally in
    a single call, so the NEXT block's own unconditional advance_phase()
    would then fire a SECOND time against a phase it never actually
    processed, skipping it too -- and that cascades: skipping Combat
    Move this way silently skipped Combat Resolution, which skipped
    Non-Combat Move, which skipped Capture Territory, which skipped
    Deploy + Income entirely, EVERY first turn (the default setting).
    Purchased units sat in pending_deployment forever, and no income was
    ever collected, for a faction's whole first turn -- a real bug this
    exact shape had until fixed this session.

    Once the loop above reaches Phase.ALLIANCES, bot.take_alliance_phase()
    runs its one optional invite/withdraw (engine.bots.alliance_policy),
    BEFORE process_game_end_check -- withdrawing to avoid the game ending
    is that same regular action, not a separate end-of-game window (see
    engine.engine.GameEngine.process_game_end_check's own docstring).

    A faction can become eliminated (victory.elimination_rule) DURING its
    own turn, not just someone else's -- process_elimination_check is
    global (checks every faction's SC count, not just whoever's turn it
    is), and the clearest real case is an ally-defended territory: the
    active faction's own units in a contested territory it owns die while
    an ALLY's co-stationed units survive, so _determine_capture_winner
    (still running as part of the active faction's OWN Capture Territory
    phase) hands that territory to the ally instead -- confirmed a real
    bug this session, reproducing on prior commits too: once that drops
    the active faction itself to <=1 Strategic Center,
    active_factions() no longer includes it, and every remaining phase
    method for THIS SAME faction (deploy_and_collect_income,
    process_game_end_check) refuses the call and raises, crashing this
    loop outright instead of just ending that faction's turn early. Every
    per-phase dispatch below is guarded on `faction in
    gs.active_factions()` for exactly this reason -- advance_phase()
    itself has no such dependency, so the phase pointer still drains
    through to Alliances normally either way; process_elimination_check()
    always runs regardless (it's global, not faction-specific, so a
    newly-inactive `faction` doesn't change anything about needing it)."""
    gs = engine.game_state
    turns_played = 0
    while not gs.game_over and turns_played < max_turns:
        faction = gs.active_faction
        if faction is None:
            break
        bot = bots[faction]

        while gs.phase != Phase.ALLIANCES:
            if faction in gs.active_factions():
                if gs.phase == Phase.PURCHASE:
                    bot.take_purchase_phase()
                elif gs.phase == Phase.COMBAT_MOVE:
                    bot.take_combat_move_phase()
                elif gs.phase == Phase.COMBAT_RESOLUTION:
                    engine.resolve_combat(faction)
                elif gs.phase == Phase.NONCOMBAT_MOVE:
                    bot.take_noncombat_move_phase()
                elif gs.phase == Phase.CAPTURE:
                    engine.process_capture_territory(faction)
                elif gs.phase == Phase.DEPLOY_INCOME:
                    engine.deploy_and_collect_income(faction)
            if gs.phase == Phase.CAPTURE:
                engine.process_elimination_check()
            engine.advance_phase()

        if faction in gs.active_factions():
            bot.take_alliance_phase()
            engine.process_game_end_check(faction)
        else:
            # Eliminated partway through its own turn -- no Alliances-
            # phase action to take (nothing left to invite/withdraw with),
            # but the game-over condition still needs checking, same as
            # process_game_end_check would have done for an active faction.
            gs.game_over = engine.would_game_end()
        turns_played += 1
        if gs.game_over:
            break
        engine.advance_turn()

    return turns_played
