extends SceneTree
## Every faction (the playable ones and the built-in Neutral and Noncombatant) has an icon that loads:
##   godot --headless --path client -s res://tests/faction_icons_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame  # autoloads (GameData) come up
	var game_data = root.get_node("GameData")
	var icons = load("res://scripts/faction_icons.gd")
	for code in game_data.owner_order():
		var tex: Texture2D = icons.get_icon(code)
		_check(tex != null and tex.get_width() == 64, "%s's icon loads at 64px" % code)
		var rect: Control = icons.make(code, 18)
		_check(rect is TextureRect and rect.tooltip_text == str(game_data.factions[code].name), "%s's icon names it" % code)
		rect.free()
	print("faction icons test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
