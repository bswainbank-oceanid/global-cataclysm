extends SceneTree
## Headless check of PopupOrder: pop-up windows share one layer, the newest opened in front, and a click on a
## partly covered one brings it forward.
##   godot --headless --path client -s res://tests/popup_order_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _window(parent: Node) -> Array:
	var w := Control.new()
	var panel := PanelContainer.new()
	w.add_child(panel)
	w.visible = false
	parent.add_child(w)
	PopupOrder.manage(w, panel)
	return [w, panel]


func _front(parent: Node) -> Node:
	return parent.get_child(parent.get_child_count() - 1)


func _initialize() -> void:
	await process_frame
	var parent := Control.new()
	root.add_child(parent)
	var armistice := _window(parent)
	var alliance := _window(parent)
	_check(armistice[0].z_index == PopupOrder.Z and alliance[0].z_index == PopupOrder.Z, "one layer for both")
	alliance[0].visible = true
	await process_frame
	armistice[0].visible = true  # (the armistice comes up after the alliance window)
	await process_frame
	_check(_front(parent) == armistice[0], "the newest window is in front")
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	alliance[1].gui_input.emit(click)
	await process_frame
	_check(_front(parent) == alliance[0], "clicking the covered one brings it forward")
	alliance[0].visible = false
	alliance[0].visible = true
	await process_frame
	_check(_front(parent) == alliance[0], "reopening puts it in front again")
	print("popup order test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
