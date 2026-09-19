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
var _battle_panel: BattlePanel
var _tile_drag_label: PanelContainer
var _tile_drag_travel := 0.0
var _tile_dragging := false
var _view_before_battles := {}  # the camera before the first auto-zoom to a battle: {pos, zoom}


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
	_setup_move_dragging()
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
	_side.move_recall.connect(Stepper.move_recall)
	_side.purchase_add.connect(Stepper.purchase_add)
	_side.purchase_remove.connect(Stepper.purchase_remove)
	Stepper.busy = func(): return _world.arrows.is_playing()

	var battle_panel := BattlePanel.new()
	_battle_panel = battle_panel
	add_child(battle_panel)
	# A battle pause first zooms to the battle and selects it; the board itself
	# opens when the player presses Next.
	Stepper.battle_focus.connect(func(preview: Dictionary):
		if _view_before_battles.is_empty():  # remember where we were, to return after the last battle
			_view_before_battles = {"pos": _cam.position, "zoom": _cam.zoom.x}
		_focus_territory(int(preview["territory_id"])))
	Stepper.combat_resolution_ended.connect(func():
		if not _view_before_battles.is_empty():
			_cam.jump_to(_view_before_battles["pos"], _view_before_battles["zoom"])
			if Dbg.args.has("shot"):
				print("[dbg] zoomed back out after combat resolution to zoom %.2f" % _cam.zoom.x)
			_view_before_battles = {})
	Stepper.battle_opened.connect(battle_panel.open)
	Stepper.battle_result.connect(battle_panel.receive_events)
	battle_panel.roll_requested.connect(Stepper.execute_open_battle)
	battle_panel.closed.connect(Stepper.release_battle)

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
		if Dbg.args.has("buy"):  # --buy=Infantry:21,Cruiser:14  (a scripted player's purchase clicks)
			await Stepper.wait_ready()
			for item in str(Dbg.args["buy"]).split(","):
				var parts := item.split(":")
				if parts[0].begins_with("-"):  # a leading "-" removes one
					Stepper.purchase_remove(parts[0].substr(1), int(parts[1]))
				else:
					Stepper.purchase_add(parts[0], int(parts[1]))
				await get_tree().create_timer(0.25).timeout
		if Dbg.args.has("move_to"):  # --move_to=<territory id>: drop the selected units there (a scripted player)
			await Stepper.wait_ready()
			await get_tree().create_timer(0.3).timeout
			Stepper.move_commit(int(Dbg.args["move_to"]))
			await get_tree().create_timer(0.6).timeout
		if Dbg.args.has("recall"):  # --recall=<unit id,...>
			for uid in str(Dbg.args["recall"]).split(","):
				Stepper.move_recall([int(uid)])
				await get_tree().create_timer(0.4).timeout
		if Dbg.args.has("select2"):  # --select2=<id>: select another space after the moves (e.g. the destination)
			_side.show_space(int(Dbg.args["select2"]))
			await get_tree().create_timer(0.3).timeout
		if Dbg.args.has("hold"):  # --hold=<seconds>: hold the submit button that long (the ring fills)
			await _side.debug_hold(float(Dbg.args["hold"]))
		if Dbg.args.has("battle_rolls"):
			# Scripted play: n actions, each opening a paused battle's board (the
			# player's go-ahead) and/or pressing the board's button once.
			for i in int(Dbg.args["battle_rolls"]):
				if Stepper.has_pending_battle():
					Stepper.button_pressed()
				if _battle_panel.visible:
					await _battle_panel.debug_press(1)
				else:
					await get_tree().create_timer(0.3).timeout
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
		if not Dbg.args.has("drag_hold"):  # --drag_hold: leave the button pressed (to capture the drag in progress)
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


# ---- dragging units to a move target -------------------------------------------

## A drag from the space whose units are selected (on the map) or from a selected
## unit tile (in the side panel) onto a green target queues that move. The map
## drag is intercepted by the camera only when it starts on the origin space, so
## panning everywhere else is unchanged.
func _setup_move_dragging() -> void:
	_cam.drag_intercept = func(p: Vector2) -> bool:
		return GameStore.human_move_active() and not GameStore.move_selected.is_empty() \
			and _world.space_at_world(_cam.screen_to_world(p)) == GameStore.move_origin
	_cam.move_drag.connect(_on_move_drag)
	GameStore.move_changed.connect(_refresh_move_targets)
	_tile_drag_label = PanelContainer.new()
	_tile_drag_label.add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	_tile_drag_label.add_child(HudStyle.label("moving", 13, HudStyle.GOLD))
	_tile_drag_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_tile_drag_label.z_index = 200
	_tile_drag_label.visible = false
	add_child(_tile_drag_label)


## A drag that started on a selected unit tile: follow the mouse (globally), show
## what is being dragged, and treat the map like the origin-drag does.
func _input(event: InputEvent) -> void:
	if not GameStore.tile_drag_armed:
		return
	if Dbg.args.has("shot") and not Dbg.injecting:
		return
	var mm := event as InputEventMouseMotion
	var mb := event as InputEventMouseButton
	if mm != null:
		_tile_drag_travel += mm.relative.length()
		if _tile_drag_travel < 6.0:
			return
		_tile_dragging = true
		_tile_drag_label.visible = true
		(_tile_drag_label.get_child(0) as Label).text = "%d unit(s)" % GameStore.move_selected.size()
		_tile_drag_label.position = mm.position + Vector2(14, 10)
		if _container.get_global_rect().has_point(mm.position):
			_drag_hover(mm.position - _container.get_global_rect().position)
		else:
			_clear_drag_preview()
	elif mb != null and mb.button_index == MOUSE_BUTTON_LEFT and not mb.pressed:
		var over_map := _container.get_global_rect().has_point(mb.position)
		if _tile_dragging and over_map:
			_drag_release(mb.position - _container.get_global_rect().position)
		else:
			_clear_drag_preview()
		GameStore.tile_drag_armed = false
		_tile_dragging = false
		_tile_drag_travel = 0.0
		_tile_drag_label.visible = false


func _on_move_drag(phase: String, p: Vector2) -> void:
	if phase == "start" or phase == "move":
		_drag_hover(p)
	elif phase == "end":
		_drag_release(p)
	else:
		_clear_drag_preview()


func _refresh_move_targets() -> void:
	_world.set_move_targets(GameStore.move_targets().keys(), -1)


## While dragging: highlight the target under the cursor and draw the arrow the
## move will get (snapped to the target, else following the cursor). True if the
## cursor is over a viable target.
func _drag_hover(screen_pos: Vector2) -> bool:
	var world := _cam.screen_to_world(screen_pos)
	var tid := _world.space_at_world(world)
	var targets := GameStore.move_targets()
	var over := targets.has(tid)
	_world.arrows.set_preview({
		"from": GameStore.move_origin, "to": tid if over else -1, "to_pos": world,
		"faction": GameStore.move_faction(),
		"count": int(targets[tid]["count"]) if over else GameStore.move_selected.size()})
	_world.set_move_targets(targets.keys(), tid if over else -1)
	return over


func _drag_release(screen_pos: Vector2) -> void:
	var tid := _world.space_at_world(_cam.screen_to_world(screen_pos))
	if GameStore.move_targets().has(tid):
		Stepper.move_commit(tid)
	_clear_drag_preview()


func _clear_drag_preview() -> void:
	_world.arrows.clear_preview()
	_refresh_move_targets()


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
