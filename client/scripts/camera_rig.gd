class_name CameraRig
extends Camera2D
## Pan/zoom for the cylindrical map: smooth cursor-anchored zoom, drag with
## inertia, WASD/arrow keys, trackpad pinch/pan. The map wraps east-west, so
## `position.x` is folded back into [0, map_w) whenever it leaves that range
## (the world draws three side-by-side copies, so the jump is invisible);
## north-south clamps at the poles. Zoom can't go out past showing the whole
## map in both directions, which is also what keeps three copies enough.

signal zoom_changed(z: float)
signal clicked(world_pos: Vector2)
## A left-button drag that started where `drag_intercept` said "mine" (e.g. on the
## space whose units are being moved) instead of panning: "start" / "move" / "end"
## with the cursor in viewport px. A press-release without movement is a click.
signal move_drag(phase: String, screen_pos: Vector2)

const MAX_ZOOM := 4.0
const ZOOM_STEP := 1.18
const KEY_PAN_SPEED := 900.0   # screen px/sec
const CLICK_SLOP := 6.0        # px of drag movement that still counts as a click
const ZOOM_SMOOTHING := 14.0
const INERTIA_DAMPING := 5.0

var target_zoom := 1.0
## True while the view is fit to the whole map (zoomed all the way out). In that
## state the zoom FOLLOWS min_zoom() as the viewport is resized, rather than
## ratcheting up to any transient size (layout briefly reports odd sizes while
## panels settle, and a ratchet would leave the view permanently zoomed in).
var _follow_min := true
var _anchor := Vector2.ZERO    # viewport-px point a USER zoom (wheel/pinch) is anchored to
var _user_zoom := false        # true while a wheel/pinch zoom is still animating
var drag_intercept := Callable()  # (viewport_pos: Vector2) -> bool: should a left-press here start a move drag?
var _move_dragging := false
var _dragging := false
var _drag_travel := 0.0
var _velocity := Vector2.ZERO  # world px/sec, inertia after a drag is released
var mouse_screen := Vector2(-1, -1)  # last known cursor position in viewport px; (-1,-1) = none yet


func _ready() -> void:
	target_zoom = zoom.x


func min_zoom() -> float:
	var vp := get_viewport_rect().size
	return maxf(vp.x / GameData.map_w, vp.y / GameData.map_h)


## Viewport-pixel position -> map-space position (unwrapped; callers fold x
## into [0, map_w) themselves where it matters).
func screen_to_world(p: Vector2) -> Vector2:
	return position + (p - get_viewport_rect().size * 0.5) / zoom.x


func jump_to(world_pos: Vector2, z: float) -> void:
	target_zoom = clampf(z, min_zoom(), MAX_ZOOM)
	_follow_min = target_zoom <= min_zoom() + 0.0005
	zoom = Vector2(target_zoom, target_zoom)
	position = world_pos
	_velocity = Vector2.ZERO
	_constrain()
	zoom_changed.emit(zoom.x)


func _unhandled_input(event: InputEvent) -> void:
	# Scripted screenshot runs ignore the real mouse/trackpad, so stray input
	# on the dev machine can't perturb a verification; only input the harness
	# itself injects (Dbg.injecting) is honoured.
	if Dbg.args.has("shot") and not Dbg.injecting:
		return
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		mouse_screen = mb.position
		match mb.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				_zoom_at(mb.position, ZOOM_STEP)
			MOUSE_BUTTON_WHEEL_DOWN:
				_zoom_at(mb.position, 1.0 / ZOOM_STEP)
			MOUSE_BUTTON_LEFT, MOUSE_BUTTON_MIDDLE, MOUSE_BUTTON_RIGHT:
				if mb.button_index == MOUSE_BUTTON_LEFT and (_move_dragging or (mb.pressed and drag_intercept.is_valid() and drag_intercept.call(mb.position))):
					if mb.pressed:
						_move_dragging = true
						_drag_travel = 0.0
						move_drag.emit("start", mb.position)
					else:
						_move_dragging = false
						if _drag_travel < CLICK_SLOP:
							move_drag.emit("cancel", mb.position)
							clicked.emit(screen_to_world(mb.position))
						else:
							move_drag.emit("end", mb.position)
					return
				if mb.pressed:
					_dragging = true
					_drag_travel = 0.0
					_velocity = Vector2.ZERO
				elif _dragging:
					_dragging = false
					if mb.button_index == MOUSE_BUTTON_LEFT and _drag_travel < CLICK_SLOP:
						clicked.emit(screen_to_world(mb.position))
	elif event is InputEventMouseMotion and _move_dragging:
		var mv := event as InputEventMouseMotion
		mouse_screen = mv.position
		_drag_travel += mv.relative.length()
		if _drag_travel >= CLICK_SLOP:
			move_drag.emit("move", mv.position)
	elif event is InputEventMouseMotion and _dragging:
		var mm := event as InputEventMouseMotion
		mouse_screen = mm.position
		_drag_travel += mm.relative.length()
		position -= mm.relative / zoom.x
		_velocity = -mm.velocity / zoom.x
		_constrain()
	elif event is InputEventMouseMotion:
		mouse_screen = (event as InputEventMouseMotion).position
	elif event is InputEventMagnifyGesture:
		_zoom_at((event as InputEventMagnifyGesture).position, (event as InputEventMagnifyGesture).factor)
	elif event is InputEventPanGesture:
		position += (event as InputEventPanGesture).delta * 12.0 / zoom.x
		_constrain()


func _zoom_at(screen_pos: Vector2, factor: float) -> void:
	_anchor = screen_pos
	_user_zoom = true
	target_zoom = clampf(target_zoom * factor, min_zoom(), MAX_ZOOM)
	_follow_min = target_zoom <= min_zoom() + 0.0005


func _process(delta: float) -> void:
	var keys := Vector2(
		Input.get_axis("ui_left", "ui_right"),
		Input.get_axis("ui_up", "ui_down"))
	if Input.is_key_pressed(KEY_A): keys.x -= 1.0
	if Input.is_key_pressed(KEY_D): keys.x += 1.0
	if Input.is_key_pressed(KEY_W): keys.y -= 1.0
	if Input.is_key_pressed(KEY_S): keys.y += 1.0
	if keys != Vector2.ZERO:
		position += keys.limit_length(1.0) * KEY_PAN_SPEED * delta / zoom.x
		_velocity = Vector2.ZERO

	if not is_equal_approx(zoom.x, target_zoom):
		var vp_center := get_viewport_rect().size * 0.5
		# Only a user zoom is anchored at the cursor. Every other zoom change
		# (the fit-to-map minimum following a viewport resize) anchors on the
		# view centre, so a panel resizing never slides the map sideways.
		var anchor := _anchor if _user_zoom else vp_center
		var old_z := zoom.x
		var world_at_anchor := position + (anchor - vp_center) / old_z
		var new_z := lerpf(old_z, target_zoom, 1.0 - exp(-ZOOM_SMOOTHING * delta))
		if absf(new_z - target_zoom) < 0.0005:
			new_z = target_zoom
		zoom = Vector2(new_z, new_z)
		position = world_at_anchor - (anchor - vp_center) / new_z
		zoom_changed.emit(new_z)
		if new_z == target_zoom:
			_user_zoom = false

	if not _dragging and _velocity.length() > 4.0:
		position += _velocity * delta
		_velocity *= exp(-INERTIA_DAMPING * delta)
	_constrain()


func _constrain() -> void:
	var lo := min_zoom()
	if _follow_min:
		target_zoom = lo  # smooth zoom-out (or a resize) eases toward the fit
	if zoom.x < lo:
		zoom = Vector2(lo, lo)
		target_zoom = maxf(target_zoom, lo)
	var half := get_viewport_rect().size * 0.5 / zoom.x
	position.x = fposmod(position.x, GameData.map_w)
	position.y = clampf(position.y, half.y, maxf(half.y, GameData.map_h - half.y))
