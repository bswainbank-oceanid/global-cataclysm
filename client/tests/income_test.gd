extends SceneTree
## Headless check of GameStore.territory_income (the top bar's MCP: the worth of the
## uncontested territory a faction holds, Strategic Center bonus included, not its cash):
##   godot --headless --path client -s res://tests/income_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var store = root.get_node("GameStore")
	var data = root.get_node("GameData")
	# A plain land territory (value > 0, no SC), a Strategic Center, and a contested territory.
	var plain := -1
	var sc := -1
	var other := -1
	for tid in data.land_ids:
		var t: Dictionary = data.territories[tid]
		if int(t.get("value", 0)) <= 0:
			continue
		if t.get("strategic_center", false):
			if sc < 0:
				sc = tid
		elif plain < 0:
			plain = tid
		elif other < 0:
			other = tid
	var territories := {}
	for tid in data.land_ids:
		territories[str(tid)] = {"territory_id": tid, "owner": null, "units": [], "contested_by": null, "pending_deployment": []}
	territories[str(plain)]["owner"] = "NAA"
	territories[str(sc)]["owner"] = "NAA"
	territories[str(other)]["owner"] = "NAA"
	territories[str(other)]["contested_by"] = ["GPC", "NAA"]
	var factions := {}
	for code in ["NAA", "GPC"]:
		factions[code] = {"code": code, "mode": "BOT", "treasury_mpc": 999, "alliance": null, "eliminated": false}
	store.set_state({"global_turn": 0, "active_faction": "NAA", "phase": "PURCHASE", "territories": territories, "factions": factions})
	var expected: int = int(data.territories[plain]["value"]) + int(data.territories[sc]["value"]) + 2
	_check(store.territory_income("NAA") == expected, "NAA's income is the value of its uncontested land plus 2 for the SC (%d vs %d)" % [store.territory_income("NAA"), expected])
	_check(store.territory_income("NAA") != 999, "and not its treasury")
	_check(store.territory_income("GPC") == 0, "a faction with no land has none")
	territories[str(other)]["contested_by"] = null
	store.set_state({"global_turn": 0, "active_faction": "NAA", "phase": "PURCHASE", "territories": territories, "factions": factions})
	_check(store.territory_income("NAA") == expected + int(data.territories[other]["value"]), "once the contest ends the territory counts again")
	# A territory the engine has switched off (it started out Defensive) is no Strategic Center
	# for whoever holds it: no bonus in the income, no star.
	territories[str(sc)]["sc_disabled"] = true
	store.set_state({"global_turn": 0, "active_faction": "NAA", "phase": "PURCHASE", "territories": territories, "factions": factions})
	_check(not store.is_sc(sc), "a switched-off territory is not an SC")
	_check(store.territory_income("NAA") == int(data.territories[plain]["value"]) + int(data.territories[sc]["value"]) + int(data.territories[other]["value"]),
		"its income is the bare value, no +2 (%d)" % store.territory_income("NAA"))
	print("income test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
