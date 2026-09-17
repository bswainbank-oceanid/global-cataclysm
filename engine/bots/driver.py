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
    becomes that phase at all that turn), so each bot call below only
    runs if GameState.phase is actually still the phase it expects --
    calling e.g. take_combat_move_phase() when the phase has already
    moved on to Combat Resolution would otherwise raise."""
    gs = engine.game_state
    turns_played = 0
    while not gs.game_over and turns_played < max_turns:
        faction = gs.active_faction
        if faction is None:
            break
        bot = bots[faction]

        if gs.phase == Phase.PURCHASE:
            bot.take_purchase_phase()
        engine.advance_phase()

        if gs.phase == Phase.COMBAT_MOVE:
            bot.take_combat_move_phase()
        engine.advance_phase()

        if gs.phase == Phase.COMBAT_RESOLUTION:
            engine.resolve_combat(faction)
        engine.advance_phase()

        if gs.phase == Phase.NONCOMBAT_MOVE:
            bot.take_noncombat_move_phase()
        engine.advance_phase()

        if gs.phase == Phase.CAPTURE:
            engine.process_capture_territory(faction)
            engine.process_elimination_check()
        engine.advance_phase()

        if gs.phase == Phase.DEPLOY_INCOME:
            engine.deploy_and_collect_income(faction)
        engine.advance_phase()

        engine.process_game_end_check(faction)
        turns_played += 1
        if gs.game_over:
            break
        engine.advance_turn()

    return turns_played
