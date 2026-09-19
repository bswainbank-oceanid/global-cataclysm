class_name MapArrows
extends Node2D
## Curved arrows for move orders: one per (origin, destination) pair, in the
## mover's faction colour, wider the more units it carries. Shown while the
## orders are QUEUED; when they execute, each arrow shortens tail-first over
## DURATION seconds -- as if the units were travelling the path.
##
## Covers combat moves, non-combat moves and the automatic return-to-base.
## Arrows are drawn at constant on-screen width (counter-scaled by 1/zoom),
## end at the two spaces' label points, and take the short way across the
## east-west seam. Bend rule: an arrow whose origin lies north/east of its
## destination bulges north/east, one from the south/west bulges south/west
## -- so a pair of opposing moves (A>B and B>A) never lie on top of each other.

const DURATION := 1.0
const SAMPLES := 40
const MOVE_KINDS := ["combat_move", "noncombat_move", "return_to_base"]

var zoom := 1.0
var _queued: Array = []   # [{from, to, faction, count}]
var _playing: Array = []  # [{arrow: Dictionary, t: float}]


func _ready() -> void:
	set_process(false)
	GameStore.state_changed.connect(queue_redraw)


func set_zoom(z: float) -> void:
	zoom = z
	queue_redraw()


## Arrows for the moves in a phase's events: [{from, to, faction, count}].
static func from_events(events: Array) -> Array:
	var groups := {}
	for e in events:
		if not MOVE_KINDS.has(str(e.get("kind", ""))):
			continue
		for o in e["orders"]:
			if not o.has("from"):
				continue  # e.g. a ride-along the engine didn't attribute an origin to
			var dest: int
			match str(e["kind"]):
				"combat_move":
					dest = int(o["path"][o["path"].size() - 1])
				"noncombat_move":
					dest = int(o["destination"])
				_:
					dest = int(o["to"])
			var src := int(o["from"])
			if src == dest:
				continue
			var key := "%s|%d|%d" % [e["faction"], src, dest]
			if not groups.has(key):
				groups[key] = {"from": src, "to": dest, "faction": str(e["faction"]), "count": 0}
			groups[key]["count"] += 1
	return groups.values()


## True while executed arrows are still shortening.
func is_playing() -> bool:
	return not _playing.is_empty()


func show_queued(arrows: Array) -> void:
	_queued = arrows
	queue_redraw()


## The queued orders were just executed: animate them away.
func play_queued() -> void:
	for a in _queued:
		_playing.append({"arrow": a, "t": 0.0})
	_queued = []
	set_process(not _playing.is_empty())
	queue_redraw()


func _process(delta: float) -> void:
	for p in _playing:
		p["t"] += delta / DURATION
	_playing = _playing.filter(func(p): return p["t"] < 1.0)
	set_process(not _playing.is_empty())
	queue_redraw()


func _draw() -> void:
	for a in _queued:
		_draw_arrow(a, 0.0)
	for p in _playing:
		_draw_arrow(p["arrow"], p["t"])


# ---- geometry ---------------------------------------------------------------

## Endpoints as world positions, the destination moved to whichever wrap copy
## is nearest the origin.
func _endpoints(a: Dictionary) -> Array:
	var p0: Vector2 = GameData.label_points[a["from"]]
	var p1: Vector2 = GameData.label_points[a["to"]]
	var dx := p1.x - p0.x
	if dx > GameData.map_w * 0.5:
		p1.x -= GameData.map_w
	elif dx < -GameData.map_w * 0.5:
		p1.x += GameData.map_w
	return [p0, p1]


## Unit vector the arrow bulges towards (see the class comment). The
## classification is antisymmetric under reversing the move, so opposing
## arrows bend to opposite sides.
static func bend_normal(origin: Vector2, dest: Vector2) -> Vector2:
	var d := dest - origin
	var ne := Vector2(1, -1)  # north-east, in screen coordinates (y grows south)
	var k := -d.dot(ne)       # > 0: the origin is north-east of the destination
	if absf(k) < 0.001:
		k = d.y                # exactly on the NW-SE diagonal: a north origin counts as north-east
	var want := 1.0 if k > 0.0 else -1.0
	var p := Vector2(-d.y, d.x).normalized()
	var best := -p
	for n in [p, -p]:
		var score: float = n.dot(ne) * want
		if absf(score) < 0.001:
			score = -n.y * want  # perpendicular is NW/SE: prefer north (or south)
		if score > 0.0:
			best = n
	return best


func _curve(p0: Vector2, p1: Vector2) -> PackedVector2Array:
	var mid := (p0 + p1) * 0.5
	var ctrl := mid + bend_normal(p0, p1) * maxf(p0.distance_to(p1) * 0.22, 8.0)
	var pts := PackedVector2Array()
	for i in SAMPLES + 1:
		var t := float(i) / SAMPLES
		pts.append(p0.lerp(ctrl, t).lerp(ctrl.lerp(p1, t), t))
	return pts


static func _point_at(pts: PackedVector2Array, cum: PackedFloat32Array, s: float) -> Vector2:
	for i in range(1, pts.size()):
		if cum[i] >= s:
			var seg := cum[i] - cum[i - 1]
			return pts[i - 1].lerp(pts[i], (s - cum[i - 1]) / seg if seg > 0.0 else 1.0)
	return pts[pts.size() - 1]


# ---- drawing ----------------------------------------------------------------

func _draw_arrow(a: Dictionary, progress: float) -> void:
	var ends := _endpoints(a)
	var col: Color = GameStore.display_color(a["faction"]).lightened(0.3)
	col.a = 1.0  # opaque, so the shaft and head overlap without a seam
	var edge := Color(0, 0, 0, 0.8)
	var inv := 1.0 / zoom
	var width_px := clampf(3.0 + int(a["count"]) * 1.5, 3.0, 22.0)
	var head_w := (width_px * 1.7 + 6.0) * inv
	var head_l := head_w * 0.9

	for copy in [-1, 0, 1]:
		var shift := Vector2(copy * GameData.map_w, 0)
		var pts := _curve(ends[0] + shift, ends[1] + shift)
		var cum := PackedFloat32Array([0.0])
		for i in range(1, pts.size()):
			cum.append(cum[i - 1] + pts[i].distance_to(pts[i - 1]))
		var total := cum[cum.size() - 1]
		var s0 := progress * total                 # the tail: moves towards the tip
		var hl := minf(head_l, total - s0)         # the head shrinks away at the very end
		if hl <= 0.5 * inv:
			continue
		var head_scale := hl / head_l

		var s_end := total - hl
		var tip := pts[pts.size() - 1]
		var base := _point_at(pts, cum, s_end)
		var dir := (tip - base).normalized()
		var side := Vector2(-dir.y, dir.x) * head_w * 0.5 * head_scale
		var head := PackedVector2Array([tip, base + side, base - side])
		var head_loop := PackedVector2Array(head)
		head_loop.append(head[0])

		# Two passes -- every dark outline first, then every fill on top -- so
		# no outline runs across the join between the shaft and the head.
		var ribbon := PackedVector2Array()
		var ribbon_fill := PackedVector2Array()
		if s_end - s0 > 0.5 * inv:
			ribbon.append(_point_at(pts, cum, s0))
			for i in pts.size():
				if cum[i] > s0 and cum[i] < s_end:
					ribbon.append(pts[i])
			ribbon.append(base)
			ribbon_fill = ribbon.duplicate()
			ribbon_fill.append(_point_at(pts, cum, s_end + hl * 0.5))  # tuck the shaft into the head
			draw_polyline(ribbon, edge, (width_px + 3.0) * inv, true)
		draw_polyline(head_loop, edge, 3.2 * inv, true)
		if not ribbon_fill.is_empty():
			draw_polyline(ribbon_fill, col, width_px * inv, true)
		draw_colored_polygon(head, col)
