extends SceneTree
## Headless check of CameraRig's touchscreen handling: a two-finger pinch zooms (and doesn't click), moving the
## two fingers together pans, and one finger is still a tap.
##   godot --headless --path client -s res://tests/touch_camera_test.gd

var _failures := 0
var _clicks := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _touch(cam, index: int, pos: Vector2, pressed: bool) -> void:
	var t := InputEventScreenTouch.new()
	t.index = index
	t.position = pos
	t.pressed = pressed
	cam._unhandled_input(t)


func _drag(cam, index: int, pos: Vector2) -> void:
	var d := InputEventScreenDrag.new()
	d.index = index
	d.position = pos
	cam._unhandled_input(d)


func _mouse(cam, pos: Vector2, pressed: bool) -> void:
	var m := InputEventMouseButton.new()
	m.button_index = MOUSE_BUTTON_LEFT
	m.position = pos
	m.pressed = pressed
	cam._unhandled_input(m)


func _initialize() -> void:
	await process_frame
	root.size = Vector2i(1280, 800)
	var cam = load("res://scripts/camera_rig.gd").new()
	root.add_child(cam)
	await process_frame
	cam.clicked.connect(func(_p): _clicks += 1)
	var before: float = cam.target_zoom
	# Two fingers spread apart: zoom in, anchored between them; no click when they lift.
	_touch(cam, 0, Vector2(600, 400), true)
	_mouse(cam, Vector2(600, 400), true)  # (the first finger's mouse events, as Godot sends them)
	_touch(cam, 1, Vector2(700, 400), true)
	_drag(cam, 1, Vector2(720, 400))
	_drag(cam, 1, Vector2(800, 400))
	_check(cam.target_zoom > before * 1.5, "spreading two fingers zooms in: %s -> %s" % [before, cam.target_zoom])
	var zoomed: float = cam.target_zoom
	_drag(cam, 1, Vector2(700, 400))
	_check(cam.target_zoom < zoomed, "pinching them together zooms out")
	# Both fingers moving together: a pan.
	var at: Vector2 = cam.position
	_drag(cam, 0, Vector2(500, 400))
	_drag(cam, 1, Vector2(600, 400))
	_check(cam.position.x > at.x, "two fingers moving left pan the map right: %s -> %s" % [at, cam.position])
	_touch(cam, 1, Vector2(600, 400), false)
	_mouse(cam, Vector2(500, 400), false)
	_touch(cam, 0, Vector2(500, 400), false)
	_check(_clicks == 0, "a pinch is never a click")
	_check(not cam._gesture, "...and it's over once every finger has lifted")
	# One finger: still a tap.
	_touch(cam, 0, Vector2(300, 300), true)
	_mouse(cam, Vector2(300, 300), true)
	_mouse(cam, Vector2(300, 300), false)
	_touch(cam, 0, Vector2(300, 300), false)
	_check(_clicks == 1, "one finger tapping is a click")
	# A finger whose lift went unseen doesn't leave the map stuck: the next first finger starts afresh.
	_touch(cam, 0, Vector2(300, 300), true)
	_touch(cam, 1, Vector2(400, 300), true)  # (a gesture... and neither lift is ever heard)
	_touch(cam, 0, Vector2(300, 300), true)
	_mouse(cam, Vector2(300, 300), true)
	_mouse(cam, Vector2(300, 300), false)
	_check(_clicks == 2, "after an unseen lift, the next tap still clicks")
	print("touch camera test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)
