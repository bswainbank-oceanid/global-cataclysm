extends SceneTree
## Headless checks for LaunchScreen's rules and the settings it sends:
##   godot --headless --path client -s res://tests/launch_screen_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _row(screen, i: int) -> Dictionary:
	return screen._rows[i]


func _pick(screen, i: int, key: String, index: int) -> void:
	var o: OptionButton = _row(screen, i)[key]
	o.select(index)
	screen._changed()


func _initialize() -> void:
	await process_frame  # autoloads (GameData loads the map data) come up
	# Loaded at runtime: compiling it needs the autoloads, which a -s script only has once running.
	var screen = load("res://scripts/launch_screen.gd").new()
	screen.remember = false  # the defaults, not whatever the last real game used
	root.add_child(screen)
	await process_frame

	# Defaults: a human, a bot, four noncombatants -- a valid game.
	_check(screen.problems().is_empty(), "the default setup is valid: %s" % str(screen.problems()))
	var s: Dictionary = screen.settings()
	_check(s["seats"].size() == 6 and s["randomize_order"] == true, "six seats, randomize turn order on by default")
	_check(s["seats"][0]["mode"] == "HUMAN" and s["seats"][1]["mode"] == "BOT" and s["seats"][2]["mode"] == "NONCOMBATANT", "default seat types")
	_check(s["seats"][0]["faction"] == "random" and s["seats"][1]["strategy"] == "random" and s["seats"][1]["behavior"] == "random",
		"faction, strategy and behavior default to random")
	_check(s["seats"][1]["alliance"] == 0, "starting alliance defaults to none")

	# The alliance rules: withdraw defaults to yes, rejoin to no; rejoining needs withdrawing.
	_check(s["can_withdraw"] == true and s["can_rejoin"] == false, "withdraw defaults to yes, rejoin to no")
	_check(not screen._can_rejoin.disabled, "rejoin is choosable while withdrawing is allowed")
	screen._can_withdraw.button_pressed = false
	screen._changed()
	_check(screen._can_rejoin.disabled, "rejoin is greyed out when withdrawing is off")
	_check(screen.settings()["can_withdraw"] == false, "the withdraw choice is sent")
	screen._can_withdraw.button_pressed = true
	screen._can_rejoin.button_pressed = true
	screen._changed()
	_check(screen.settings()["can_rejoin"] == true and screen.settings()["can_withdraw"] == true, "the rejoin choice is sent")
	screen._can_rejoin.button_pressed = false
	screen._changed()

	# At least two players.
	_pick(screen, 1, "mode", 3)  # the bot becomes Noncombatant
	_check(not screen.problems().is_empty(), "one player alone can't start")
	_check(screen._start.disabled, "Start is disabled then")
	_pick(screen, 1, "mode", 1)
	_check(screen.problems().is_empty() and not screen._start.disabled, "two players can")

	# Zero humans is fine; the human item is disabled on other seats while one seat has it.
	_check((_row(screen, 1)["mode"] as OptionButton).is_item_disabled(0), "Human is unavailable on the other seats while seat 1 is the human")
	_pick(screen, 0, "mode", 1)  # seat 1 -> Bot: no humans
	_check(screen.problems().is_empty(), "a game with no humans is valid")
	_check(not (_row(screen, 1)["mode"] as OptionButton).is_item_disabled(0), "Human is available again once nobody is the human")
	_pick(screen, 0, "mode", 0)

	# Strategy and behavior are for bots only; alliance for players only.
	_check((_row(screen, 0)["strategy"] as OptionButton).disabled, "a human has no strategy")
	_check(not (_row(screen, 1)["strategy"] as OptionButton).disabled, "a bot has a strategy")
	_check((_row(screen, 2)["alliance"] as OptionButton).disabled, "a noncombatant seat has no alliance")
	_pick(screen, 1, "mode", 2)  # Defense
	_check((_row(screen, 1)["alliance"] as OptionButton).disabled and (_row(screen, 1)["strategy"] as OptionButton).disabled, "a defense seat is neither")
	_pick(screen, 1, "mode", 1)

	# Factions can be chosen once.
	_pick(screen, 0, "faction", 1)  # NAA (first faction in order)
	var fac: OptionButton = _row(screen, 1)["faction"]
	_check(fac.is_item_disabled(1), "a faction picked elsewhere is unavailable")
	_check(not (_row(screen, 0)["faction"] as OptionButton).is_item_disabled(1), "...but still available to the seat that has it")
	_check(screen.settings()["seats"][0]["faction"] == screen._faction_of(_row(screen, 0)) and screen._faction_of(_row(screen, 0)) != "random", "the chosen faction is sent by code")
	_pick(screen, 0, "faction", 0)

	# Alliances: two or more members, and not every player. (A two-player game has no room for one:
	# its maximum alliance size is 1, which also switches the starting-alliance choice off.)
	_pick(screen, 0, "alliance", 1)
	_check((_row(screen, 0)["alliance"] as OptionButton).disabled and screen.problems().is_empty(), "two players: no alliances to choose")
	_pick(screen, 2, "mode", 1)  # a third player
	_pick(screen, 0, "alliance", 1)
	_check(not screen.problems().is_empty(), "Alliance 1 with a single member is rejected")
	_pick(screen, 1, "alliance", 1)
	_check(screen.problems().is_empty(), "two of three players in Alliance 1 is fine: %s" % str(screen.problems()))
	_pick(screen, 2, "alliance", 1)
	_check(not screen.problems().is_empty(), "an alliance of every player is rejected")
	_pick(screen, 2, "alliance", 2)
	_check(not screen.problems().is_empty(), "Alliance 2 alone (one member) is rejected")
	_pick(screen, 2, "alliance", 0)
	var sent: Dictionary = screen.settings()
	_check(sent["seats"][0]["alliance"] == 1 and sent["seats"][1]["alliance"] == 1 and sent["seats"][2]["alliance"] == 0, "alliance numbers are sent")

	# A noncombatant seat's leftover alliance choice isn't sent.
	_pick(screen, 2, "alliance", 2)
	_pick(screen, 2, "mode", 3)
	_check(screen.settings()["seats"][2]["alliance"] == 0, "non-players send no alliance")

	# Bots always play the heuristic AI: no choice is offered or sent (the server's default).
	_check(not screen.settings()["seats"][1].has("ai") and not _row(screen, 1).has("ai"), "no Bot AI choice")

	# Maximum alliance size: 1 .. players-1, default 3 (1 = no alliances, no Alliances phase).
	var size: OptionButton = screen._max_alliance
	_pick(screen, 2, "mode", 3)  # back to two players
	_check(size.disabled and size.item_count == 1 and size.get_item_text(0) == "1" and screen.settings()["max_alliance_size"] == 1, "two players: the only size is 1")
	_pick(screen, 2, "mode", 1)  # three players: 1 or 2
	_check(not size.disabled and size.item_count == 2 and size.get_item_text(0) == "1" and size.get_item_text(1) == "2", "three players: sizes 1 and 2")
	_check(screen.settings()["max_alliance_size"] == 2, "the default 3 is held to the players-1 limit")
	_pick(screen, 3, "mode", 1)
	_pick(screen, 4, "mode", 1)
	_pick(screen, 5, "mode", 1)  # six players: 1..5
	_check(size.item_count == 5 and size.get_item_text(4) == "5", "six players: sizes 1 to 5")
	_check(size.get_item_text(size.selected) == "3" and screen.settings()["max_alliance_size"] == 3, "the default is 3")
	size.select(4)
	size.item_selected.emit(4)
	_check(screen.settings()["max_alliance_size"] == 5, "the chosen size is sent")
	_pick(screen, 5, "mode", 3)
	_pick(screen, 4, "mode", 3)  # four players: sizes 1..3
	_check(size.item_count == 3 and screen.settings()["max_alliance_size"] == 3, "the size follows the player count (%d)" % int(screen.settings()["max_alliance_size"]))
	size.select(1)
	size.item_selected.emit(1)  # size 2
	_pick(screen, 2, "alliance", 0)
	_pick(screen, 0, "alliance", 1)
	_pick(screen, 1, "alliance", 1)
	_check(screen.problems().is_empty(), "an alliance of two fits a maximum of 2")
	_pick(screen, 2, "alliance", 1)
	_check(not screen.problems().is_empty(), "an alliance of three does not fit a maximum of 2: %s" % str(screen.problems()))
	size.select(2)
	size.item_selected.emit(2)  # size 3
	_check(screen.problems().is_empty(), "...but fits 3")
	size.select(0)
	size.item_selected.emit(0)  # size 1: no alliances at all
	_check((_row(screen, 0)["alliance"] as OptionButton).disabled and screen.settings()["seats"][0]["alliance"] == 0 and screen.problems().is_empty(),
		"size 1: nobody can be in a starting alliance")
	_check(screen.settings()["max_alliance_size"] == 1, "and 1 is sent")

	# First-turn options: no Combat Moves, Non-Combat Moves allowed, by default.
	_check(screen.settings()["allow_combat_first_turn"] == false and screen.settings()["allow_noncombat_first_turn"] == true, "first-turn defaults: combat no, non-combat yes")
	screen._combat_first.button_pressed = true
	screen._noncombat_first.button_pressed = false
	_check(screen.settings()["allow_combat_first_turn"] == true and screen.settings()["allow_noncombat_first_turn"] == false, "the checkboxes are sent")

	# A new scenario: Human / Bot / Not playing seats, per-seat settings and a Neutral row.
	screen._combat_first.button_pressed = false
	screen._noncombat_first.button_pressed = true
	screen.set_seat_specs({"total": 150,
		"players": [{"key": "territory_value", "min": 0, "max": 150, "default": null},
			{"key": "initial_mpc", "min": 0, "max": 1000, "default": 125},
			{"key": "units_mpc", "min": 0, "max": 1000, "default": 125},
			{"key": "promotions", "min": 0, "max": 20, "default": 3},
			{"key": "scs", "min": 0, "max": 10, "default": 3}],
		"neutral": [{"key": "territory_value", "min": 0, "max": 150, "default": null},
			{"key": "units_mpc", "min": 0, "max": 1000, "default": 100},
			{"key": "promotions", "min": 0, "max": 20, "default": 0},
			{"key": "scs", "min": 0, "max": 10, "default": 0}]})
	_check(not screen._sc_section.visible and not screen._neutral_cells[0].visible, "a fixed scenario hides the new scenario's settings")
	screen.select_scenario("new")
	_check(screen._sc_section.visible and screen._neutral_cells[0].visible, "a new scenario shows them, with a Neutral row")
	_check((_row(screen, 2)["mode"] as OptionButton).item_count == 3 and screen._mode_of(_row(screen, 5)) == "NOT_PLAYING",
		"seats are Human, Bot or Not playing")
	var players: int = screen._players()
	var each := 150 / players
	_check(int(_row(screen, 0)["territory_value"].value) == each, "a player's territory defaults to an even split (%d)" % each)
	_check(int(screen._neutral_row["territory_value"].value) == 150 - each * players, "the Neutral pool defaults to what is left")
	var ns: Dictionary = screen.settings()
	_check(ns["scenario"]["kind"] == "new" and not ns["seats"][0].has("territory_value") and ns["seats"][0]["units_mpc"] == 125,
		"a default territory is left to the server; the other settings are sent")
	_check(not ns["seats"][5].has("units_mpc"), "a seat not playing sends no settings")
	_row(screen, 0)["territory_value"].value = 40
	_check(screen.settings()["seats"][0]["territory_value"] == 40, "an edited territory value is sent")
	_row(screen, 0)["units_mpc"].value = 160
	_check(int(_row(screen, 0)["initial_mpc"].value) == 160, "raising the units MPC past the initial MPC raises the initial MPC")
	_row(screen, 0)["initial_mpc"].value = 100
	_check(int(_row(screen, 0)["units_mpc"].value) == 100, "lowering the initial MPC below the units MPC lowers the units MPC")
	screen._neutral_row["scs"].value = 2
	_check(screen.settings()["scenario"]["neutral"]["scs"] == 2, "the Neutral row is sent")
	_row(screen, 1)["territory_value"].value = 150
	_check(screen._territory_note.text.contains("scaled down"), "more than the map holds is flagged as scaled down: %s" % screen._territory_note.text)
	screen.select_scenario("fixed")
	_check(not screen.settings().has("scenario") and screen._mode_of(_row(screen, 5)) == "NONCOMBATANT", "back to the fixed scenario")


	# Saved scenario setups: listed after the two built-in entries; picking one fills the screen.
	_check(not screen._save_button.visible and not screen._info.visible, "the fixed scenario can't be saved and has no name")
	screen.select_scenario("new")
	_check(screen._save_button.visible and screen._save_button.disabled, "a new scenario can be saved once it has a name")
	screen._name.text = "Duel"
	screen._changed()
	_check(not screen._save_button.disabled and screen._delete_button.disabled, "named: Save on, nothing saved to delete")
	var saved: Dictionary = screen.setup()
	_check(saved["id"] == null and saved["name"] == "Duel" and saved["settings"]["scenario"]["kind"] == "new", "Save sends a new setup")
	var stored: Dictionary = saved["settings"].duplicate(true)
	stored["seats"][1]["strategy"] = "adversarial"
	stored["randomize_order"] = false
	stored["scenario"]["options"]["sc_bonus"] = 4
	screen.set_setups([{"id": "Setup_001", "name": "Duel", "description": "Two of us", "settings": stored}], "Setup_001")
	_check(screen._scenario.item_count == 4 and screen._scenario.is_item_separator(2) and screen._scenario.get_item_text(3) == "Duel",
		"saved setups follow a separator")
	_check(str(screen._scenario.get_item_metadata(screen._scenario.selected)) == "Setup_001" and screen._setup_id == "Setup_001",
		"the setup just saved is selected")
	_check(not screen.saves_as_new() and screen._save_button.text == "SAVE SCENARIO SETUP" and not screen._delete_button.disabled,
		"the same name updates it, and it can be deleted")
	screen.select_scenario("new")
	_check(screen._setup_id == "" and screen._name.text == "", "New scenario is unnamed")
	screen._scenario.select(3)
	screen._scenario.item_selected.emit(3)
	var back: Dictionary = screen.settings()
	_check(screen._name.text == "Duel" and screen._description.text == "Two of us", "picking it shows its name and description")
	_check(back["seats"][1]["strategy"] == "adversarial" and back["randomize_order"] == false, "...and its settings")
	_check(back["scenario"]["options"]["sc_bonus"] == 4, "...including the scenario-wide options: %s" % str(back["scenario"]["options"]))
	_check(back["seats"] == stored["seats"], "the seats come back as saved:
%s
%s" % [str(back["seats"]), str(stored["seats"])])
	screen._name.text = "Duel II"
	screen._changed()
	_check(screen.saves_as_new() and screen._save_button.text == "SAVE AS NEW SCENARIO SETUP" and screen.setup()["id"] == "Setup_001",
		"a new name saves a new setup")
	screen.set_setups([], null)
	_check(screen._setup_id == "" and screen._scenario.item_count == 2 and screen._is_new(), "after a delete: an unsaved new scenario")
	screen.select_scenario("fixed")

	# Rounds until armistice proposal: under Rules, default 10, 0..100, sent and restored.
	_check(int(screen.settings()["armistice_rounds"]) == 10, "armistice rounds default to 10")
	_check(screen._armistice_rounds.max_value == 100.0 and screen._armistice_rounds.min_value == 0.0, "0 to 100")
	screen._armistice_rounds.value = 25
	_check(int(screen.settings()["armistice_rounds"]) == 25, "the chosen rounds are sent")
	var restored: Dictionary = screen.settings()
	restored["armistice_rounds"] = 40
	screen.apply_settings(restored)
	_check(int(screen.settings()["armistice_rounds"]) == 40, "a saved setup restores it")

	print("launch screen test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
