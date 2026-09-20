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

	# Unit: at most one roll per press; the presses that roll nothing are the start-of-side
	# pauses. Every roll is revealed exactly once and the battle finishes.
	var m := _load(path)
	var presses := 0
	var rolled := 0
	var beats := 0
	while not m.finished and presses < 200:
		m.press(_all(BattleModel.Resolve.UNIT))
		presses += 1
		_check(m.last_rolls.size() <= 1, "Unit press reveals at most one roll")
		rolled += m.last_rolls.size()
		if m.last_rolls.is_empty() and not m.finished:
			beats += 1
			_check(not m.prev_lines.is_empty(), "a side-start pause says what is up")
	_check(m.finished and rolled == total_rolls, "Unit mode reveals all %d rolls (got %d)" % [total_rolls, rolled])
	_check(beats >= m.rounds.size(), "Unit mode pauses at the start of the sides (%d pauses over %d rounds)" % [beats, m.rounds.size()])

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
			_check(m.last_rolls.size() <= 1, "attacker set to Unit rolls one at a time")
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
	var bonus_guard := 0
	while mb.round_label != "Round 1" and bonus_guard < 20:  # (an air superiority round may come first)
		mb.press(_all(BattleModel.Resolve.ROUND))
		bonus_guard += 1
		if mb.round_label == "Air Superiority":
			continue
	_check(mb.round_title().begins_with("Round 1") and mb.round_title().contains("Defender") and mb.round_title().contains("amphibious landing"), "Round 1 label names side and reason: " + mb.round_title())
	mb.press(_all(BattleModel.Resolve.ROUND))
	if not mb.finished and mb.round_index >= 1 and int(mb.rounds[mb.round_index]["round"]) >= 2:
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
		if m.round_index == same_round and not m.last_rolls.is_empty() and not m._round_closed:  # (a round end settles XP from the engine)
			var e: Dictionary = m.last_rolls[0]
			if bool(e.get("hit", false)) and int(e["target_hp_after"]) > 0:
				_check(int(m.units[int(e["unit_id"])]["xp"]) > before[int(e["unit_id"])] or m.round_index != same_round, "a hit shows XP at once: %s r%d xp %d -> %d" % [e["unit_type"], m.round_index, before[int(e["unit_id"])], int(m.units[int(e["unit_id"])]["xp"])])
				xp_checked = true
		guard += 1
	# Round and Entire Battle run straight through the side starts: no pause presses.
	for mode in [BattleModel.Resolve.ROUND, BattleModel.Resolve.ENTIRE_BATTLE]:
		m = _load(path)
		guard = 0
		while not m.finished and guard < 20:
			m.press(_all(mode))
			guard += 1
			_check(m.finished or not m.last_rolls.is_empty(), "%s never spends a press on a side-start pause" % BattleModel.RESOLVE_NAMES[mode])

	# The text box and the button label.
	m = _load(path)
	var action: Dictionary = m.next_action(_all(BattleModel.Resolve.UNIT))
	_check(str(action["label"]).begins_with("Roll ") and str(action["text"]).begins_with("Next:"), "before the first roll: %s / %s" % [action["label"], action["text"]])
	m.press(_all(BattleModel.Resolve.UNIT))
	_check(m.prev_lines.size() >= 1 and str(m.prev_lines[0]).contains("rolled"), "a roll pulse says what was rolled: %s" % str(m.prev_lines))
	var saw_start_label := false
	guard = 0
	while not m.finished and guard < 200:
		action = m.next_action(_all(BattleModel.Resolve.UNIT))
		if str(action["label"]).begins_with("Start "):
			saw_start_label = true
			_check(str(action["text"]).contains("is up"), "a side-start label comes with what that side will do: %s" % action["text"])
		m.press(_all(BattleModel.Resolve.UNIT))
		guard += 1
	_check(saw_start_label, "the button announces the start of a side")
	_check(m.next_action(_all(BattleModel.Resolve.UNIT))["label"] == "End Battle", "the final state's button is End Battle")
	_check(str(m.prev_lines.back()).contains("The battle is over"), "the last pulse reports the end: %s" % str(m.prev_lines))
	_check(m.end_reason in ["eliminated", "no_targets", "rounds"], "the end reason comes through (%s)" % m.end_reason)

	# Units with no legal target are reported and never roll.
	var skips_path := "res://tests/fixtures/battle_skips.json"
	m = _load(skips_path)
	var reported := {}
	var expected := 0
	for r in m.rounds:
		for sd in r["sides"]:
			expected += sd["skips"].size()
	_check(expected > 0, "the skips fixture has units without a target")
	guard = 0
	while not m.finished and guard < 300:
		var round_before := m.round_index
		m.press(_all(BattleModel.Resolve.UNIT))
		for sk in m.last_skips:
			reported["%d|%d" % [round_before if round_before >= 0 else 0, sk["unit_id"]]] = sk
		guard += 1
	_check(m.finished, "the skips battle finishes")
	var revealed := 0
	for r in m.rounds:
		for sd in r["sides"]:
			revealed += int(sd["skip_next"])
	_check(revealed == expected, "every passed-over unit is reported once (%d of %d)" % [revealed, expected])
	for r in m.rounds:
		for sd in r["sides"]:
			for sk in sd["skips"]:
				for e in r["rolls"]:
					if int(e["unit_id"]) == int(sk["unit_id"]) and str(e["side"]) == str(sd["side"]):
						_check(false, "a unit without a target must not roll in that round")

	print("model test: saw_hit=%s, xp_checked=%s, failures=%d" % [saw_hit, xp_checked, _failures])
	quit(1 if _failures > 0 else 0)
