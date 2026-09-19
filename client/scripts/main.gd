extends Control
## Root of the client. Builds the scene tree in code, following the mockup:
## a top bar (title/round/phase + one stats panel per faction), and below it
## the map area beside the right-hand selection/log panels. The map lives in
## a SubViewport so the camera math only ever sees the map area itself.

var _container: SubViewportContainer
var _viewport: SubViewport
var _world: MapWorld
var _cam: CameraRig
var _hover_label: Label
var _top: TopBar
var _side: SidePanel


func _ready() -> void:
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("separation", 6)
	add_child(root)

	_top = TopBar.new()
	root.add_child(_top)

	var body := HBoxContainer.new()
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", 8)
	root.add_child(body)

	var map_area := Control.new()
	map_area.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	map_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	map_area.clip_contents = true
	body.add_child(map_area)

	_side = SidePanel.new()
	body.add_child(_side)

	_container = SubViewportContainer.new()
	_container.stretch = true
	_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	map_area.add_child(_container)

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
	_world.selected_changed.connect(_side.show_space)

	_hover_label = Label.new()
	_hover_label.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	_hover_label.position = Vector2(10, -26)
	_hover_label.add_theme_font_size_override("font_size", 13)
	_hover_label.add_theme_color_override("font_color", Color.WHITE)
	_hover_label.add_theme_color_override("font_outline_color", Color.BLACK)
	_hover_label.add_theme_constant_override("outline_size", 4)
	_hover_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	map_area.add_child(_hover_label)

	_side.territory_clicked.connect(_focus_territory)
	Stepper.busy = func(): return _world.arrows.is_playing()

	var settings_panel := SettingsPanel.new()
	settings_panel.visible = false
	settings_panel.z_index = 100
	settings_panel.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	settings_panel.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	settings_panel.offset_top = 104
	settings_panel.offset_right = -10
	add_child(settings_panel)
	_top.settings_pressed.connect(func(): settings_panel.visible = not settings_panel.visible)
	Stepper.log_line.connect(_side.log_line)
	Stepper.queue_shown.connect(_side.show_queue)
	Stepper.executed.connect(_side.log_events)
	# The executed reply arrives just before the next phase's queue: play the
	# old arrows out, then show the new ones.
	Stepper.executed.connect(func(_h, _e): _world.arrows.play_queued())
	Stepper.queue_shown.connect(func(_h, _s, events): _world.arrows.show_queued(MapArrows.from_events(events)))

	await get_tree().process_frame
	await get_tree().process_frame
	_start_view()


func _start_view() -> void:
	var vp := _viewport.size
	var z := maxf(vp.x / GameData.map_w, vp.y / GameData.map_h)
	_cam.jump_to(Vector2(GameData.map_w, GameData.map_h) * 0.5, z)
	_world.set_zoom(_cam.zoom.x)
	if Dbg.args.has("state"):
		GameStore.set_state(JSON.parse_string(FileAccess.get_file_as_string(Dbg.args["state"])))
	if Dbg.args.has("badges"):
		_world.units.set_style(MapUnits.Style[Dbg.args["badges"].to_upper()])
	if Dbg.args.has("cam"):
		var c: PackedStringArray = Dbg.args["cam"].split(",")
		_cam.jump_to(Vector2(float(c[0]), float(c[1])), float(c[2]))
		_world.set_zoom(_cam.zoom.x)
	if Dbg.args.has("select"):
		_world.select(int(Dbg.args["select"]))
	if Dbg.args.has("hover"):
		_world.force_hover(int(Dbg.args["hover"]))
	_refresh_info(-1)
	if Dbg.args.has("select"):
		_side.show_space(int(Dbg.args["select"]))
	if Dbg.args.has("server"):
		var url: String = Dbg.args["server"]
		Net.start("" if url == "true" else url)
		if Dbg.args.has("steps"):
			await Stepper.press(int(Dbg.args["steps"]))
		else:
			await Stepper.wait_ready()
	await _scripted_input()
	Dbg.scene_ready = true


## Injects real input events through the window (so they go through the
## SubViewportContainer exactly as a user's would), for Dbg's --wheel/
## --drag/--click. Used only for scripted verification runs.
func _scripted_input() -> void:
	Dbg.injecting = true
	await _inject_all()
	Dbg.injecting = false


func _inject_all() -> void:
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


## Select a territory and centre the map on it, zoomed to fit it (never
## further out than the 1.0x zoom where the detailed badges show).
func _focus_territory(tid: int) -> void:
	_world.select(tid)
	var box: Rect2 = GameData.bboxes[tid]
	var vp := _viewport.size
	var z := minf(vp.x / maxf(box.size.x * 1.6, 1.0), vp.y / maxf(box.size.y * 1.6, 1.0))
	_cam.jump_to(GameData.label_points[tid], clampf(z, 1.0, 3.0))


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
	_hover_label.text = _describe(_world.highlight.hovered)
