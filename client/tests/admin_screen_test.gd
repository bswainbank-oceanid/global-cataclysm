extends SceneTree
## Headless checks for the Admin panel (AdminScreen):
##   godot --headless --path client -s res://tests/admin_screen_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame  # (the autoloads come up first)
	var screen = load("res://scripts/admin_screen.gd").new()
	root.add_child(screen)
	await process_frame
	var sent := []
	screen.request.connect(func(m): sent.append(m))
	screen.set_overview({"stats": {"players": 3, "max_users": 100}})

	# Most players: a number typed in, then Save at once (no Enter; Save takes no focus) -- the typed one is sent.
	var line: LineEdit = screen._max.get_line_edit()
	line.select_all()
	line.delete_text(0, line.text.length())
	line.insert_text_at_caret("50")
	await process_frame  # (a player's typing and their click are never in the same frame)
	var save: Button = null
	for b in screen._max.get_parent().get_children():
		if b is Button:  # (the row: the box, then Save)
			save = b
	save.pressed.emit()
	var saves := sent.filter(func(m): return m.get("type") == "admin_set_max_users")
	_check(saves.size() == 1 and int(saves[0]["max"]) == 50, "Save sends the number typed: %s" % [saves])

	screen.queue_free()
	quit(1 if _failures > 0 else 0)
