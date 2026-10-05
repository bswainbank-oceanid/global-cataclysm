extends SceneTree
## Headless checks for the launcher as a multi-player server's New Game and Scenarios (LaunchScreen's
## multi mode): the list, what each button asks the server for, and any number of humans.
##   godot --headless --path client -s res://tests/launch_multi_test.gd
## Exits non-zero on any failure.

var _failures := 0
var _sent: Array = []


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _seats(first_mode: String, randomize := true) -> Dictionary:
	var seats := [{"mode": first_mode, "faction": "random"}, {"mode": "BOT", "faction": "NAA"}]
	for i in 4:
		seats.append({"mode": "NOT_PLAYING", "faction": "random"})
	return {"seats": seats, "randomize_order": randomize, "scenario": {"kind": "new", "options": {}, "neutral": {}}}


func _listing(personal_duel := false) -> Array:
	var duel := _seats("HUMAN")
	var mine := _seats("BOT", false)
	return [
		{"id": "fixed", "kind": "fixed", "name": null, "description": "", "settings": null, "defaults": null, "personal": false},
		{"id": "new", "kind": "new", "name": null, "description": "", "settings": null, "defaults": null, "personal": false},
		{"id": "Setup_001", "kind": "shared", "name": "Duel", "description": "two", "personal": personal_duel,
			"settings": _seats("HUMAN", false) if personal_duel else duel, "defaults": duel},
		{"id": "S_000001", "kind": "own", "name": "Mine", "description": "my own", "settings": mine, "defaults": mine, "personal": false},
	]


func _last() -> Dictionary:
	return _sent.back() if not _sent.is_empty() else {}


func _pick(screen, entry: String) -> void:
	screen._entry_picked(entry)


func _initialize() -> void:
	await process_frame
	var screen = load("res://scripts/launch_screen.gd").new()
	screen.remember = false
	root.add_child(screen)
	await process_frame
	screen.server_request.connect(func(m): _sent.append(m))
	screen.open_multi({"scenarios": _listing(), "new_scenario": {}, "selected": null}, false)

	# The list: GC72, New scenario, then the shared ones, then the player's own.
	var labels := []
	for i in screen._scenario.item_count:
		labels.append(screen._scenario.get_item_text(i))
	_check(labels == ["Global Cataclysm: 1972", "New scenario", "Shared scenarios", "Duel", "My scenarios", "Mine"],
		"the list: %s" % str(labels))
	_check(not screen._resume.visible and screen._back_button.visible and screen._start.visible, "Back and Start; no Resume")
	var account = root.get_node("Account")
	account.multi = true
	account.user = {"id": "U_000001", "admin": false}
	screen._changed()
	_check(not screen._ai_col.visible, "no Bot type column for a player")
	account.user = {"id": "U_000001", "admin": true}
	screen._changed()
	_check(screen._ai_col.visible, "an admin sees it")
	account.multi = false
	account.user = {}

	# Any number of humans.
	screen._rows[1]["mode"].select(0)
	screen._changed()
	_check(screen.problems().is_empty(), "two humans are fine here: %s" % str(screen.problems()))
	_check(not screen._rows[2]["mode"].is_item_disabled(0), "and Human stays choosable for every seat")

	# GC72: Save keeps the player's own settings for it; Start creates the game.
	screen._start.activated.emit()
	_check(_last().get("type") == "create_game" and _last()["scenario"] == {"kind": "fixed", "id": "fixed"}, "Start: create_game (GC72)")
	screen._save_button.activated.emit()
	_check(_last().get("type") == "save_settings" and _last()["scenario_id"] == "fixed", "Save on GC72: save_settings")
	_check(screen._reset_button.disabled, "Reset is off with no saved settings")

	# A shared scenario: its settings fill the screen; Save is the player's settings unless renamed.
	_pick(screen, "Setup_001")
	_check(screen.settings()["seats"][0]["mode"] == "HUMAN" and screen._name.text == "Duel", "a shared scenario fills the screen")
	_check(not screen._delete_button.visible, "no Delete for a shared scenario")
	screen._save_button.activated.emit()
	_check(_last().get("type") == "save_settings" and _last()["scenario_id"] == "Setup_001", "Save: my settings for it")
	screen._name.text = "Duel 2"
	screen._changed()
	_check(screen._save_button.text == "SAVE AS NEW SCENARIO", "renamed: Save as New Scenario")
	screen._save_button.activated.emit()
	_check(_last().get("type") == "save_scenario" and _last()["scenario"]["id"] == null and _last()["scenario"]["name"] == "Duel 2",
		"...which saves a new scenario of my own")
	screen._start.activated.emit()
	_check(_last()["scenario"] == {"kind": "shared", "id": "Setup_001"}, "Start: from the shared scenario")

	# The player's saved settings for it come back; Reset asks the server to forget them.
	screen.set_scenarios(_listing(true), "Setup_001")
	_check(screen.settings()["randomize_order"] == false, "the saved settings fill the screen")
	_check(not screen._reset_button.disabled, "and Reset is on")
	screen._reset_button.activated.emit()
	_check(_last() == {"type": "reset_settings", "scenario_id": "Setup_001"}, "Reset: reset_settings")

	# An own scenario: Save updates it (or saves a new one when renamed); Delete is offered; Reset is local.
	_pick(screen, "S_000001")
	_check(screen._delete_button.visible and not screen._delete_button.disabled, "Delete for my own scenario")
	screen._save_button.activated.emit()
	_check(_last().get("type") == "save_scenario" and _last()["scenario"]["id"] == "S_000001", "Save updates it")
	screen._delete_button.activated.emit()
	_check(_last() == {"type": "delete_scenario", "id": "S_000001"}, "Delete: delete_scenario")
	screen._randomize.button_pressed = true
	screen._changed()
	var count := _sent.size()
	screen._reset_button.activated.emit()
	_check(_sent.size() == count and screen.settings()["randomize_order"] == false, "Reset on my own: its last saved version")
	screen._start.activated.emit()
	_check(_last()["scenario"] == {"kind": "own", "id": "S_000001"}, "Start: from my own scenario")

	# Scenarios mode (admins): the shared ones as they are, Save and Delete change them, no Start.
	_sent.clear()
	screen.open_multi({"scenarios": _listing(true), "new_scenario": {}, "selected": null}, true)
	labels = []
	for i in screen._scenario.item_count:
		labels.append(screen._scenario.get_item_text(i))
	_check(not labels.has("Mine"), "no own scenarios in Scenarios mode")
	_check(not screen._start.visible and not screen._reset_button.visible, "no Start, no Reset")
	_pick(screen, "Setup_001")
	_check(screen.settings()["randomize_order"] == true, "the shared scenario's own settings, not the admin's")
	screen._save_button.activated.emit()
	_check(_last().get("type") == "save_shared" and _last()["scenario"]["id"] == "Setup_001", "Save: save_shared")
	screen._delete_button.activated.emit()
	_check(_last() == {"type": "delete_shared", "id": "Setup_001"}, "Delete: delete_shared")
	_pick(screen, "fixed")
	_check(not screen._save_button.visible, "GC72 can't be saved over")
	quit(1 if _failures > 0 else 0)
