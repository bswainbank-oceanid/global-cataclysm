extends SceneTree
## Headless checks for BattleModel against a real battle fixture (a preview plus
## the events the server sent for it):
##   godot --headless --path client -s res://tests/battle_model_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _load(path: String) -> BattleModel:
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	var m := BattleModel.from_preview(data["preview"])
	m.load_events(data["events"])
	return m


func _all(mode: int) -> Dictionary:
	return {"attacker": mode, "defender": mode}


func _initialize() -> void:
	var path := "res://tests/fixtures/battle_multi.json"
	var total_rolls := 0
	var m0 := _load(path)
	for r in m0.rounds:
		total_rolls += r["rolls"].size()
	_check(total_rolls > 0, "fixture has rolls")

	# Unit: one roll per press, and it finishes after exactly total_rolls presses.
	var m := _load(path)
	var presses := 0
	while not m.finished and presses < 100:
		m.press(_all(BattleModel.Resolve.UNIT))
		presses += 1
		if not m.finished:
			_check(m.last_rolls.size() == 1, "Unit press reveals one roll")
	_check(m.finished and presses == total_rolls, "Unit mode: %d presses for %d rolls (got %d)" % [total_rolls, total_rolls, presses])

	# Entire Battle: one press to the end.
	m = _load(path)
	m.press(_all(BattleModel.Resolve.ENTIRE_BATTLE))
	_check(m.finished, "Entire Battle finishes in one press")
	for id in m.unit_order:
		_check(m.units[id]["present"], "every unit is back at the end")
		_check((m.units[id]["mark"] == BattleModel.Mark.DEAD) == m.eliminated.has(id), "X exactly on the eliminated units")

	# Round: one press per round.
	m = _load(path)
	var round_presses := 0
	while not m.finished and round_presses < 20:
		m.press(_all(BattleModel.Resolve.ROUND))
		round_presses += 1
	_check(m.finished and round_presses <= m.rounds.size(), "Round mode: at most one press per round")

	# Side / Unit Type never cross a side boundary.
	for mode in [BattleModel.Resolve.SIDE, BattleModel.Resolve.UNIT_TYPE]:
		m = _load(path)
		var n := 0
		while not m.finished and n < 100:
			m.press(_all(mode))
			n += 1
			var sides := {}
			for e in m.last_rolls:
				sides[e["side"]] = true
			_check(sides.size() <= 1, "%s chunks stay within one side" % BattleModel.RESOLVE_NAMES[mode])
		_check(m.finished, "%s mode finishes" % BattleModel.RESOLVE_NAMES[mode])

	# Different settings per side: the side about to roll decides the chunk size.
	m = _load(path)
	var mixed := {"attacker": BattleModel.Resolve.UNIT, "defender": BattleModel.Resolve.ENTIRE_BATTLE}
	var guard := 0
	while not m.finished and guard < 100:
		var next_side := ""
		if m.round_index < 0 or m.roll_index >= m.rounds[m.round_index]["rolls"].size():
			pass  # next press starts a round; the first roller decides
		else:
			next_side = str(m.rounds[m.round_index]["rolls"][m.roll_index]["side"])
		m.press(mixed)
		if next_side == "attacker":
			_check(m.last_rolls.size() == 1, "attacker set to Unit rolls one at a time")
		guard += 1
	_check(m.finished, "mixed settings finish")

	# Marks: a HIT survivor is marked HIT until the next press.
	m = _load(path)
	var saw_hit := false
	guard = 0
	while not m.finished and guard < 100:
		m.press(_all(BattleModel.Resolve.UNIT))
		for e in m.last_rolls:
			if bool(e.get("hit", false)):
				var t: Dictionary = m.units[int(e["target_unit_id"])]
				_check(t["mark"] != BattleModel.Mark.NONE or m.finished, "a hit target carries a mark")
				saw_hit = true
		guard += 1
	# A first-round combat bonus is named in the label, for round 1 only.
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	data["preview"]["round1_bonus"] = {"side": "defender", "reason": "amphibious landing"}
	var mb := BattleModel.from_preview(data["preview"])
	mb.load_events(data["events"])
	_check(mb.round_title().begins_with("Ready") and mb.round_title().contains("amphibious landing"), "Ready label names the bonus")
	mb.press(_all(BattleModel.Resolve.UNIT))
	_check(mb.round_title().begins_with("Round 1") and mb.round_title().contains("Defender") and mb.round_title().contains("amphibious landing"), "Round 1 label names side and reason: " + mb.round_title())
	mb.press(_all(BattleModel.Resolve.ROUND))
	mb.press(_all(BattleModel.Resolve.ROUND))
	if not mb.finished and mb.round_index >= 1:
		_check(not mb.round_title().contains("bonus"), "later rounds carry no bonus: " + mb.round_title())

	# XP shows the moment a unit deals damage (promotion waits for the round's end).
	m = _load(path)
	var xp_checked := false
	guard = 0
	while not m.finished and guard < 100:
		var before := {}
		for id in m.unit_order:
			before[id] = int(m.units[id]["xp"])
		var same_round := m.round_index
		m.press(_all(BattleModel.Resolve.UNIT))
		if m.round_index == same_round and not m.last_rolls.is_empty():
			var e: Dictionary = m.last_rolls[0]
			if bool(e.get("hit", false)) and int(e["target_hp_after"]) > 0:
				_check(int(m.units[int(e["unit_id"])]["xp"]) > before[int(e["unit_id"])] or m.round_index != same_round, "a hit shows XP at once")
				xp_checked = true
		guard += 1
	print("model test: saw_hit=%s, xp_checked=%s, failures=%d" % [saw_hit, xp_checked, _failures])
	quit(1 if _failures > 0 else 0)
