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
    exact shape had until fixed this session."""
    gs = engine.game_state
    turns_played = 0
    while not gs.game_over and turns_played < max_turns:
        faction = gs.active_faction
        if faction is None:
            break
        bot = bots[faction]

        while gs.phase != Phase.ALLIANCES:
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
                engine.process_elimination_check()
            elif gs.phase == Phase.DEPLOY_INCOME:
                engine.deploy_and_collect_income(faction)
            engine.advance_phase()

        engine.process_game_end_check(faction)
        turns_played += 1
        if gs.game_over:
            break
        engine.advance_turn()

    return turns_played
