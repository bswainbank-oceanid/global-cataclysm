extends Control
## Root of the client. Builds the scene tree in code: a SubViewport holding
## the map world and camera (so the HUD can later frame it with panels and
## the camera math only ever sees the map area), with a small debug readout
## on top until the real HUD replaces it.

var _container: SubViewportContainer
var _viewport: SubViewport
var _world: MapWorld
var _cam: CameraRig
var _info: Label


func _ready() -> void:
	_container = SubViewportContainer.new()
	_container.stretch = true
	_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_container)

	_viewport = SubViewport.new()
	_viewport.handle_input_locally = true
	_container.add_child(_viewport)

	_world = MapWorld.new()
	_viewport.add_child(_world)
	_cam = CameraRig.new()
	_viewport.add_child(_cam)
	_world.cam = _cam

	_cam.zoom_changed.connect(_world.set_zoom)
	_cam.clicked.connect(func(p: Vector2): _world.select(_world.space_at_world(p)))
	_container.mouse_entered.connect(func(): _world.set_hover_enabled(true))
	_container.mouse_exited.connect(func(): _world.set_hover_enabled(false))
	_world.hovered_changed.connect(_refresh_info)
	_world.selected_changed.connect(_refresh_info)

	_info = Label.new()
	_info.position = Vector2(12, 8)
	_info.add_theme_color_override("font_color", Color.WHITE)
	_info.add_theme_color_override("font_outline_color", Color.BLACK)
	_info.add_theme_constant_override("outline_size", 4)
	add_child(_info)

	await get_tree().process_frame
	_start_view()


func _start_view() -> void:
	var vp := _viewport.size
	var z := maxf(vp.x / GameData.map_w, vp.y / GameData.map_h)
	_cam.jump_to(Vector2(GameData.map_w, GameData.map_h) * 0.5, z)
	_world.set_zoom(_cam.zoom.x)
	if Dbg.args.has("cam"):
		var c: PackedStringArray = Dbg.args["cam"].split(",")
		_cam.jump_to(Vector2(float(c[0]), float(c[1])), float(c[2]))
		_world.set_zoom(_cam.zoom.x)
	if Dbg.args.has("select"):
		_world.select(int(Dbg.args["select"]))
	if Dbg.args.has("hover"):
		_world.force_hover(int(Dbg.args["hover"]))
	_refresh_info(-1)
	await _scripted_input()
	Dbg.scene_ready = true


## Injects real input events through the window (so they go through the
## SubViewportContainer exactly as a user's would), for Dbg's --wheel/
## --drag/--click. Used only for scripted verification runs.
func _scripted_input() -> void:
	if Dbg.args.has("wheel"):
		var w: PackedStringArray = Dbg.args["wheel"].split(",")
		var pos := Vector2(float(w[0]), float(w[1]))
		var steps := int(w[2])
		for i in absi(steps):
			var ev := InputEventMouseButton.new()
			ev.button_index = MOUSE_BUTTON_WHEEL_UP if steps > 0 else MOUSE_BUTTON_WHEEL_DOWN
			ev.position = pos
			ev.global_position = pos
			ev.pressed = true
			Input.parse_input_event(ev)
			await get_tree().process_frame
		for i in 60:  # let the smooth zoom settle
			await get_tree().process_frame
	if Dbg.args.has("drag"):
		var d: PackedStringArray = Dbg.args["drag"].split(",")
		var a := Vector2(float(d[0]), float(d[1]))
		var b := Vector2(float(d[2]), float(d[3]))
		_inject_button(a, true)
		await get_tree().process_frame
		var steps := 12
		for i in range(1, steps + 1):
			var mm := InputEventMouseMotion.new()
			mm.position = a.lerp(b, float(i) / steps)
			mm.global_position = mm.position
			mm.relative = (b - a) / steps
			mm.button_mask = MOUSE_BUTTON_MASK_LEFT
			Input.parse_input_event(mm)
			await get_tree().process_frame
		_inject_button(b, false)
		await get_tree().process_frame
	if Dbg.args.has("click"):
		var c: PackedStringArray = Dbg.args["click"].split(",")
		var p := Vector2(float(c[0]), float(c[1]))
		_inject_button(p, true)
		await get_tree().process_frame
		_inject_button(p, false)
		await get_tree().process_frame


func _inject_button(pos: Vector2, pressed: bool) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.position = pos
	ev.global_position = pos
	ev.pressed = pressed
	Input.parse_input_event(ev)


func _describe(tid: int) -> String:
	if tid < 0:
		return ""
	var t: Dictionary = GameData.territories[tid]
	var owner := GameStore.owner_of(tid)
	var line := "%d. %s  (%s)" % [tid, t["name"], t["type"]]
	if owner != "":
		line += "  owner: %s" % owner
	if t.get("strategic_center", false):
		line += "  [SC]"
	return line


func _refresh_info(_tid: int) -> void:
	var hov := _world.highlight.hovered
	var sel := _world.highlight.selected
	_info.text = "hover: %s\nselected: %s" % [_describe(hov), _describe(sel)]
