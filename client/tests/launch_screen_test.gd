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

	# Alliances: two or more members, and not every player.
	_pick(screen, 0, "alliance", 1)
	_check(not screen.problems().is_empty(), "an alliance of one is rejected (also: it would be every player)")
	_pick(screen, 2, "mode", 1)  # a third player
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

	print("launch screen test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
