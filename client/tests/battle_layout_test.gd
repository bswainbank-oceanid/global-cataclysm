extends SceneTree
## Headless checks for the Battle Board's two layouts (defense rows / die bands)
## and for a mid-battle promotion moving a unit up a row:
##   godot --headless --path client -s res://tests/battle_layout_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame  # autoloads (Settings) exist from here on
	var settings = root.get_node("Settings")  # autoloads can't be named in a -s script
	var panel = load("res://scripts/battle_panel.gd").new()
	var u := {"die": "D8", "defense": 7}
	var t := {"die": null, "defense": 6}
	var d5 := {"die": "D6", "defense": 5}
	var d10 := {"die": "D12", "defense": 10}

	settings.battle_layout = 0  # BattleLayout.DEFENSE
	_check(panel._rows().size() == 6, "defense layout has one row per defense value 5..10")
	_check(panel._die_w() == 0.0, "defense layout has no die columns")
	for by_die in [true, false]:  # the rolling side no longer slides into its die's band
		_check(panel._row_of(u, by_die) == 2, "defense 7 sits in the 7 row")
		_check(panel._row_of(t, by_die) == 1, "a transport (no die) sits in the 6 row")
		_check(panel._row_of(d5, by_die) == 0, "defense 5 sits in the first row")
		_check(panel._row_of(d10, by_die) == 5, "defense 10 sits in the last row")

	settings.battle_layout = 1  # BattleLayout.DIE_BANDS
	_check(panel._rows().size() == 7, "classic layout keeps its seven rows")
	_check(panel._die_w() > 0.0, "classic layout keeps the die columns")
	_check(panel._row_of(t, true) == 0, "classic: transports in the '-' row")
	_check(panel._row_of(u, false) == 3, "classic: defense 7 by defense")
	_check(panel._row_of({"die": "D6", "defense": 9}, true) != panel._row_of({"die": "D6", "defense": 9}, false), "classic: the rolling side slides into its die's band")
	panel.free()

	# A promotion during a battle raises the unit's defense by one at once.
	var data = JSON.parse_string(FileAccess.get_file_as_string("res://tests/fixtures/battle_multi.json"))
	var m := BattleModel.from_preview(data["preview"])
	m.load_events(data["events"])
	var promoted := 0
	var before := {}
	for id in m.unit_order:
		before[id] = m.units[id].duplicate()
	var guard := 0
	while not m.finished and guard < 200:
		m.press({"attacker": BattleModel.Resolve.UNIT, "defender": BattleModel.Resolve.UNIT})
		guard += 1
		for id in m.unit_order:
			var now: Dictionary = m.units[id]
			var was: Dictionary = before[id]
			if bool(now["promoted"]) and not bool(was["promoted"]) and not bool(now["cargo"]):
				promoted += 1
				_check(int(now["defense"]) == mini(int(was["defense"]) + 1, 10), "a promoted unit's defense goes up by one")
			before[id] = now.duplicate()
	print("promotions seen: %d" % promoted)
	quit(1 if _failures > 0 else 0)
