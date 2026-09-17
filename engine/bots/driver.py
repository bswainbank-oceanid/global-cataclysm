"""
Drives a bot-controlled game to completion (or a safety-capped number of
turns) using GameEngine's real public phase API -- the same sequence a
human-driven UI would follow, just with a RandomBot supplying every
order. Not itself part of the rules engine; a convenience for
integration testing and for producing a stats.GameStats report of a
full game.
"""


def play_to_completion(engine, bots, max_turns=500):
    """bots: {faction_code: RandomBot}, one entry per HUMAN/BOT faction
    in play. Drives full turns (Purchase through Alliances, per
    turn_order) for whichever faction is GameState.active_faction,
    calling advance_phase() between phases and advance_turn() at the end
    of each faction's turn, until GameState.game_over or `max_turns`
    full turns have been played -- a safety cap, since two bots with no
    lookahead can in principle stalemate forever; this just stops the
    loop rather than hanging. Returns the number of turns actually
    played."""
    gs = engine.game_state
    turns_played = 0
    while not gs.game_over and turns_played < max_turns:
        faction = gs.active_faction
        if faction is None:
            break
        bot = bots[faction]

        bot.take_purchase_phase()
        engine.advance_phase()

        bot.take_combat_move_phase()
        engine.advance_phase()

        engine.resolve_combat(faction)
        engine.advance_phase()

        bot.take_noncombat_move_phase()
        engine.advance_phase()

        engine.process_capture_territory(faction)
        engine.process_elimination_check()
        engine.advance_phase()

        engine.deploy_and_collect_income(faction)
        engine.advance_phase()

        engine.process_game_end_check(faction)
        turns_played += 1
        if gs.game_over:
            break
        engine.advance_turn()

    return turns_played
