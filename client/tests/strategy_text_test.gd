extends SceneTree
## Headless checks for the Strategy Log's and Game Log's text for purchases that could not deploy where
## they were bought for, and the round in a turn's headings:
##   godot --headless --path client -s res://tests/strategy_text_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _plain(bbcode: String) -> String:
	var re := RegEx.new()
	re.compile("\\[[^\\]]*\\]")
	return re.sub(bbcode, "", true)


func _initialize() -> void:
	await process_frame  # autoloads (GameData) come up
	var event_text = load("res://scripts/event_text.gd")
	var strategy_text = load("res://scripts/strategy_text.gd")
	var moved := {"kind": "deploy_redirected", "faction": "UER", "from": 40, "to": 39,
		"units": [{"unit_type": "Infantry", "qty": 6}], "reason": "territory_lost"}
	var lost := {"kind": "deploy_lost", "faction": "UER", "territory_id": 40,
		"units": [{"unit_type": "Infantry", "qty": 1}], "reason": "territory_lost"}
	var air := {"kind": "deploy_redirected", "faction": "UER", "from": 131, "to": 39,
		"units": [{"unit_type": "Fighter", "qty": 2}], "reason": "no_carrier_room"}

	var line: String = _plain(event_text.describe(moved))
	_check(line == "UER: 6x Infantry bought for Czechia deploy to Italy instead (Czechia was lost this turn)", line)
	line = _plain(event_text.describe(lost))
	_check(line.contains("Infantry bought for Czechia are lost"), line)
	line = _plain(event_text.describe(air))
	_check(line.contains("2x Fighter bought for") and line.contains("no room on an own Aircraft Carrier"), line)

	var phase: String = _plain(strategy_text.describe({"kind": "strategy_phase", "faction": "UER", "phase": "DEPLOY_INCOME",
		"choices": [], "rejected": [], "deploy_changes": [moved]}))
	_check(phase.contains("UER · DEPLOY") and phase.contains("6x Infantry bought for Czechia deploy to Italy"), phase)

	var stats := {"territory_mpc": 30, "unit_value": 150, "unit_count": 30, "scs": 3}
	var start: String = _plain(strategy_text.describe({"kind": "strategy_turn_start", "faction": "UER", "round": 5,
		"style": "Strategic", "stats": stats, "change": null}))
	_check(start.contains("UER · ROUND 5 · TURN START"), start)
	var review := {"purchase": [], "combat": [], "noncombat": [], "rejected": [], "deploy_changes": [moved],
		"idle_units": [], "no_resources": []}
	var end: String = _plain(strategy_text.describe({"kind": "strategy_turn_end", "faction": "UER", "round": 5,
		"style": "Strategic", "stats": stats, "change": stats, "review": review}))
	_check(end.contains("UER · ROUND 5 · END OF TURN"), end)
	_check(end.contains("Purchases deployed elsewhere or lost") and end.contains("deploy to Italy instead"), end)

	print("strategy text test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
