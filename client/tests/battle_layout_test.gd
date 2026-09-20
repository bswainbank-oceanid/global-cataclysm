extends SceneTree
## Headless checks for the Battle Board's rows and dice layout, and for a mid-battle
## promotion moving a unit up a row:
##   godot --headless --path client -s res://tests/battle_layout_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame  # autoloads (Settings) exist from here on
	var panel = load("res://scripts/battle_panel.gd").new()
	var u := {"die": "D8", "defense": 7}
	var t := {"die": null, "defense": 6}
	_check(panel.ROWS.size() == 6, "one row per defense value 5..10")
	_check(panel._row_of(u) == 2, "defense 7 sits in the 7 row")
	_check(panel._row_of(t) == 1, "a transport (no die) sits in the 6 row")
	_check(panel._row_of({"die": "D6", "defense": 5}) == 0, "defense 5 sits in the first row")
	_check(panel._row_of({"die": "D12", "defense": 10}) == 5, "defense 10 sits in the last row")
	_check(panel._row_of({"die": "D12", "defense": 11}) == 5, "a defense above 10 stays in the last row")
	panel.free()

	# Dice fill their row's cell in a grid; grids taller than the cell spill into the
	# neighbouring rows, never overlap each other, and shrink to fit the table.
	var panel2 = load("res://scripts/battle_panel.gd").new()
	var y := 52.0
	for i in 6:
		panel2._row_y.append(y)
		panel2._row_h.append(56.0)
		y += 56.0
	var dice := func(n: int) -> Array:
		var out := []
		for i in n:
			out.append({})
		return out
	var lay: Dictionary = panel2._dice_layout([2], {2: dice.call(1)}, 110.0)
	_check(lay["scale"] == 1.0 and lay["cols"] == 2, "one die: full size, a two-wide grid")
	var b: Dictionary = lay["blocks"][2]
	_check(absf(float(b["top"]) + float(b["height"]) * 0.5 - (52.0 + 2 * 56.0 + 28.0)) < 0.5, "one die is centred in its row")
	lay = panel2._dice_layout([2], {2: dice.call(6)}, 110.0)  # 3 lines of dice: taller than the 56px row
	b = lay["blocks"][2]
	_check(float(b["height"]) > 56.0 and float(b["top"]) < 52.0 + 2 * 56.0, "six dice spill above their row")
	# Neighbouring rows with several dice each: no overlap, in order, inside the table.
	lay = panel2._dice_layout([1, 2, 3], {1: dice.call(4), 2: dice.call(5), 3: dice.call(4)}, 110.0)
	var prev_bottom := 0.0
	for r in [1, 2, 3]:
		var blk: Dictionary = lay["blocks"][r]
		_check(float(blk["top"]) >= prev_bottom, "row %d's dice start below the row above's" % r)
		prev_bottom = float(blk["top"]) + float(blk["height"])
	_check(prev_bottom <= 52.0 + 6 * 56.0, "and end inside the table")
	# Far too many dice for the table at full size: they shrink.
	lay = panel2._dice_layout([0, 1, 2, 3, 4, 5], {0: dice.call(8), 1: dice.call(8), 2: dice.call(8), 3: dice.call(8), 4: dice.call(8), 5: dice.call(8)}, 110.0)
	_check(float(lay["scale"]) < 1.0, "a huge roll shrinks the dice (%.2f)" % float(lay["scale"]))
	panel2.free()

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
			var gained := int(now["promotions"]) - int(was["promotions"])
			if gained > 0 and not bool(now["cargo"]):
				promoted += gained
				_check(int(now["defense"]) == mini(int(was["defense"]) + gained, 10), "each promotion adds one defense (cap 10)")
			before[id] = now.duplicate()
	print("promotions seen: %d" % promoted)
	quit(1 if _failures > 0 else 0)
