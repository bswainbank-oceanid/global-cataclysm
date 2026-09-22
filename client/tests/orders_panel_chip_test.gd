extends SceneTree
## Headless check for OrdersPanel._choice's faction-colour chip (the vertical stripe on a Diplomacy
## row, e.g. "Invite NAA"): it must be a small, fixed-size rect that never stretches past the row
## button it's drawn on -- reported bug: it used to leak out of the button's box.
##   godot --headless --path client -s res://tests/orders_panel_chip_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var panel = load("res://scripts/orders_panel.gd").new()
	root.add_child(panel)
	await process_frame

	var b: Control = panel._choice("Invite NAA", "NAA", false, true, "tip", func(): pass, false)
	root.add_child(b)
	await process_frame

	var chip: ColorRect = null
	for c in b.get_children():
		if c is ColorRect:
			chip = c
	_check(chip != null, "the button has a faction-colour chip child")
	# The actual bug: PRESET_LEFT_WIDE (anchor_top=0, anchor_bottom=1 -- full-height stretch) was left
	# in place after also setting a fixed position/size, so Godot's layout system recomputed the rect
	# from those STRETCHY anchors on the next resize, extending the chip well past the button's own
	# height. A chip that never stretches has every anchor at the Control default of 0.
	_check(chip.anchor_top == 0.0 and chip.anchor_bottom == 0.0 and chip.anchor_left == 0.0 and chip.anchor_right == 0.0,
		"the chip's anchors are all 0 (fixed offsets, never stretched by a parent resize): top=%s bottom=%s" % [chip.anchor_top, chip.anchor_bottom])
	_check(chip.size == Vector2(6, 20), "the chip stays a small fixed 6x20 rect: %s" % chip.size)
	# Resize the button (as the real VBoxContainer layout pass would) and confirm the chip does NOT
	# grow with it -- this is what actually reproduced the "leaking out of the box" bug.
	b.size = Vector2(300, 120)
	await process_frame
	_check(chip.size == Vector2(6, 20), "resizing the button doesn't stretch the chip: %s" % chip.size)
	_check(chip.position.y + chip.size.y <= b.size.y, "the chip stays within the button's bounds after resize")

	print("orders panel chip test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
