extends SceneTree
## Headless checks for the announcement panel and the announcements the stepper builds:
##   godot --headless --path client -s res://tests/announcement_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var store = root.get_node("GameStore")
	var stepper = root.get_node("Stepper")
	var window = load("res://scripts/announcement_window.gd").new()
	root.add_child(window)
	await process_frame
	_check(not window.visible and not store.announcement_open, "nothing is announced at first")

	# Announcements queue up and are acknowledged one at a time; the game waits meanwhile.
	var got := []
	stepper.announced.connect(func(items: Array): got.append(items))
	stepper._announce([
		{"kind": "purchase", "faction": "NAA", "orders": [], "total_cost": 0},
		{"kind": "faction_eliminated", "faction": "UE"},
		{"kind": "alliance_joined", "turn": 3, "faction": "NAA", "target": "GPC", "tag": "ALLIANCE_1", "new_alliance": true},
		{"kind": "alliance_joined", "turn": 9, "faction": "NAA", "target": "AAC", "tag": "ALLIANCE_1", "new_alliance": false},
		{"kind": "alliance_withdrawal", "turn": 12, "faction": "GPC", "tag": "ALLIANCE_1", "former_members": ["AAC", "GPC", "NAA"]},
		{"kind": "alliance_declined", "turn": 13, "faction": "NAA", "target": "UER"},
	])
	_check(got.size() == 1 and got[0].size() == 4, "an elimination, two joins and a withdrawal are announced; purchases and refusals are not (%d)" % (got[0].size() if got.size() == 1 else -1))
	_check(str(got[0][0]["title"]).contains("UE") and str(got[0][0]["body"]).contains("eliminated"), "the elimination names the faction")
	_check(str(got[0][1]["title"]) == "New alliance" and str(got[0][2]["title"]).contains("AAC"), "a new alliance and a joining are told apart")
	_check(str(got[0][3]["body"]).contains("AAC") and str(got[0][3]["body"]).contains("NAA") and not str(got[0][3]["body"]).contains("with [b]Global"), "a withdrawal names the former allies")

	# (the window listens to the stepper, so it already has them)
	_check(window.visible and store.announcement_open, "the panel opens and holds the game")
	_check(window._ok.text == "OK  (3 more)", "and says how many more are waiting: %s" % window._ok.text)
	window.acknowledge()
	_check(window.visible and window._ok.text == "OK  (2 more)", "OK moves on to the next")
	window.acknowledge()
	window.acknowledge()
	_check(window.visible and window._ok.text == "OK", "the last one has a plain OK")
	window.acknowledge()
	_check(not window.visible and not store.announcement_open, "the last OK closes the panel and lets the game go on")

	# A new announcement while none is up shows at once; a new game clears the lot.
	window.add([{"title": "Game over", "body": "x", "color": Color.WHITE}])
	_check(window.visible, "a single announcement shows")
	window.clear()
	_check(not window.visible and not store.announcement_open, "a new game clears announcements")
	# The player's own invitation being declined is announced; a bot's is not.
	store.set_state({"global_turn": 0, "active_faction": "NAA", "phase": "ALLIANCES", "territories": {}, "factions": {
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false}}})
	got.clear()
	stepper._announce([
		{"kind": "alliance_declined", "turn": 4, "faction": "UE", "target": "NAA"},
		{"kind": "alliance_declined", "turn": 4, "faction": "NAA", "target": "UE"}])
	_check(got.size() == 1 and got[0].size() == 1, "only the player's declined invitation is news")
	_check(str(got[0][0]["title"]).begins_with("UE declines") and str(got[0][0]["body"]).contains("your invitation"), "it names who declined: %s" % str(got[0][0]["title"]))
	window.clear()
	print("announcement test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
