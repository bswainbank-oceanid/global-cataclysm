class_name HoldButton
extends Button
## A button that can demand a press-and-HOLD before it fires -- for irreversible
## orders, where a stray click must not submit. With hold_seconds == 0 it is an
## ordinary button; otherwise a ring fills around the button as the mouse (or
## Space) is held, and `activated` fires the moment it is full. Releasing early
## drains the ring and cancels. Either way the one signal to listen to is
## `activated` -- not `pressed`.

signal activated

const RING_COLOR := Color(0.45, 1.0, 0.55)
const DRAIN_SPEED := 3.0   # progress per second while released

var hold_seconds := 0.0
var progress := 0.0        # 0..1 of the current hold
var _holding := false
var _fired := false        # this hold already fired; needs a release before it can again


func _ready() -> void:
	button_down.connect(_start_hold)
	button_up.connect(_stop_hold)
	pressed.connect(func():
		if hold_seconds <= 0.0:
			activated.emit())
	set_process(false)


func _start_hold() -> void:
	if disabled or hold_seconds <= 0.0:
		return
	_holding = true
	_fired = false
	set_process(true)


func _stop_hold() -> void:
	_holding = false
	_fired = false


func _process(delta: float) -> void:
	if _holding and not _fired:
		progress = minf(progress + delta / hold_seconds, 1.0)
		if progress >= 1.0:
			_fired = true
			activated.emit()
	elif not _holding:
		progress = maxf(progress - delta * DRAIN_SPEED, 0.0)
		if progress <= 0.0:
			set_process(false)
	queue_redraw()


## Space does the same as the mouse: held for hold_seconds, or an ordinary press.
func _unhandled_key_input(event: InputEvent) -> void:
	if not is_visible_in_tree() or disabled:
		return
	var k := event as InputEventKey
	if k == null or k.keycode != KEY_SPACE or k.echo:
		return
	get_viewport().set_input_as_handled()
	if hold_seconds <= 0.0:
		if k.pressed:
			activated.emit()
		return
	if k.pressed:
		_start_hold()
	else:
		_stop_hold()


func _draw() -> void:
	if progress <= 0.0:
		return
	# The ring: the button's outline, filled clockwise from the top centre.
	var r := Rect2(Vector2(2, 2), size - Vector2(4, 4))
	var c := r.position + Vector2(r.size.x * 0.5, 0)
	var pts := PackedVector2Array([
		c, r.position + Vector2(r.size.x, 0), r.position + r.size, r.position + Vector2(0, r.size.y), r.position, c])
	var total := 0.0
	for i in range(1, pts.size()):
		total += pts[i].distance_to(pts[i - 1])
	var want := total * progress
	var out := PackedVector2Array([pts[0]])
	var run := 0.0
	for i in range(1, pts.size()):
		var seg := pts[i].distance_to(pts[i - 1])
		if run + seg >= want:
			out.append(pts[i - 1].lerp(pts[i], (want - run) / maxf(seg, 0.001)))
			break
		out.append(pts[i])
		run += seg
	draw_polyline(out, Color(0, 0, 0, 0.6), 7.0)
	draw_polyline(out, RING_COLOR, 4.0, true)
