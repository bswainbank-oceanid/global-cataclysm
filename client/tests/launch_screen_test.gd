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

	# Defaults: a human, a bot, four neutrals -- a valid game.
	_check(screen.problems().is_empty(), "the default setup is valid: %s" % str(screen.problems()))
	var s: Dictionary = screen.settings()
	_check(s["seats"].size() == 6 and s["randomize_order"] == true, "six seats, randomize turn order on by default")
	_check(s["seats"][0]["mode"] == "HUMAN" and s["seats"][1]["mode"] == "BOT" and s["seats"][2]["mode"] == "NEUTRAL", "default seat types")
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
	_pick(screen, 1, "mode", 3)  # the bot becomes Neutral
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
	_check((_row(screen, 2)["alliance"] as OptionButton).disabled, "a neutral seat has no alliance")
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

	# A neutral seat's leftover alliance choice isn't sent.
	_pick(screen, 2, "alliance", 2)
	_pick(screen, 2, "mode", 3)
	_check(screen.settings()["seats"][2]["alliance"] == 0, "non-players send no alliance")

	# Bot AI: the heuristic bot by default, the random baseline on request; only bots have one.
	_check(screen.settings()["seats"][1]["ai"] == "strategy", "a bot plays the heuristic AI by default")
	_check(not (_row(screen, 1)["ai"] as OptionButton).disabled and (_row(screen, 0)["ai"] as OptionButton).disabled, "a bot seat can choose; a human's can't")
	_pick(screen, 1, "ai", 1)
	_check(screen.settings()["seats"][1]["ai"] == "random", "the random baseline can be chosen: %s" % str(screen.settings()["seats"][1]["ai"]))
	_pick(screen, 1, "ai", 0)

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

	print("launch screen test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
