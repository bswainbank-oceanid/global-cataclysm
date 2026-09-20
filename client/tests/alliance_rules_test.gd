extends SceneTree
## Headless checks for GameStore's alliance-rule helpers (which factions the player
## can't invite, and why):
##   godot --headless --path client -s res://tests/alliance_rules_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _factions() -> Dictionary:
	var f := {}
	for code in ["NAA", "UE", "GPC", "AAC"]:
		f[code] = {"mode": "BOT", "eliminated": false, "alliance": null, "former_allies": []}
	f["NAA"]["mode"] = "HUMAN"
	return f


func _initialize() -> void:
	await process_frame
	var store = root.get_node("GameStore")

	var f := _factions()
	store.state = {"factions": f, "can_withdraw_from_alliances": true, "can_rejoin_alliances": false}
	_check(store.can_withdraw_from_alliances() and not store.can_rejoin_alliances(), "the rules are read from the state")
	store.state["can_withdraw_from_alliances"] = false
	store.state["can_rejoin_alliances"] = true
	_check(not store.can_withdraw_from_alliances() and store.can_rejoin_alliances(), "...both ways")

	# UE left an alliance with NAA; GPC is allied with AAC. NAA is alone.
	f = _factions()
	f["UE"]["former_allies"] = ["NAA"]
	f["GPC"]["alliance"] = "ALLIANCE_1"
	f["AAC"]["alliance"] = "ALLIANCE_1"
	store.state = {"factions": f, "can_withdraw_from_alliances": true, "can_rejoin_alliances": false}
	var out: Array = store.uninvitable_reasons("NAA", ["NAA"], [])
	var by := {}
	for pair in out:
		by[pair[0]] = pair[1]
	_check(by.has("UE") and str(by["UE"]).contains("rejoining is off"), "a former ally is explained (rejoining off): %s" % str(by))
	_check(by.has("GPC") and str(by["GPC"]).contains("already in an alliance"), "an allied faction is explained")
	_check(not by.has("NAA"), "the player is never listed")

	# With rejoining on, the former ally is eligible (the server lists it), so nothing to explain.
	store.state["can_rejoin_alliances"] = true
	out = store.uninvitable_reasons("NAA", ["NAA"], ["UE"])
	var codes := []
	for pair in out:
		codes.append(pair[0])
	_check(not codes.has("UE"), "an eligible faction is not listed")
	_check(codes.has("GPC") and codes.has("AAC"), "the allied pair is still explained")

	print("alliance rules test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
