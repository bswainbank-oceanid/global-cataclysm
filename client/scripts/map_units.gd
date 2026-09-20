class_name MapUnits
extends Node2D
## Unit badges, one group per occupied space, drawn at constant on-screen
## size just below the space's label. Several factions in one space (a
## contested space) sit side by side. Style is ADAPTIVE (decided with the
## user after comparing the three candidates): quiet Flag badges at world
## zoom, per-type Strip badges once zoomed in far enough that a space is
## roomy. Category is kept only as a debug comparison (`--badges=category`).

enum Style { FLAG, STRIP, CATEGORY }

const FONT_SIZE := 12
const CATEGORY_ICON := {"Land": "Infantry", "Sea": "Cruiser", "Air": "Fighter"}

const STRIP_MIN_ZOOM := 1.0
const STRIP_SCALE := 2.0   # zoomed-in badges are drawn at this multiple of the Flag size
const ROW_H := 20.0
const ROW_GAP := 5.0       # between the units row and the queued-purchases row
const GAP := 3.0           # between the value marker and the faction badges in a row
const DISC_W := 20.0       # value marker: faction-coloured disc...
const DISC_R := 8.5
const STAR_W := 28.0       # ...inside a gold star for Strategic Centers
const STAR_R := 14.0
const STAR_DISC_R := 7.5

var forced_style := -1  # -1 = adaptive; otherwise a Style, for debug comparisons
var zoom := 1.0
var pulse_spaces := {}  # territory id -> true: the player's spaces with a unit that can still move this phase
var _pulse_layer: Node2D


func _ready() -> void:
	GameStore.state_changed.connect(queue_redraw)
	GameStore.state_changed.connect(_refresh_pulse)
	GameStore.move_changed.connect(_refresh_pulse)
	_pulse_layer = _PulseLayer.new()
	_pulse_layer.units = self
	_pulse_layer.show_behind_parent = true  # the glow sits behind the badges
	add_child(_pulse_layer)
	_refresh_pulse()


## During a move phase of the player's, the spaces where at least one of their units
## could still make a legal move (the server's options leave out units already ordered).
func _refresh_pulse() -> void:
	pulse_spaces = {}
	if GameStore.human_move_active():
		for uid in GameStore.human_move["options"]:
			pulse_spaces[int(GameStore.human_move["options"][uid]["origin"])] = true
	_pulse_layer.set_process(not pulse_spaces.is_empty())
	_pulse_layer.queue_redraw()


## Where the faction's badge group sits in a space, in the badge row's own units (the row
## is centred under the label point, scaled by _badge_scale() / zoom -- see _draw).
func group_rect(tid: int, owner: String) -> Rect2:
	var groups := _groups(GameStore.stacks(tid))
	var marker_w := _marker_width(tid)
	var sizes: Array = []
	var total_w := marker_w
	for g in groups:
		var sz := _group_size(g[1])
		sizes.append(sz)
		total_w += sz.x + (GAP if total_w > 0.0 else 0.0)
	var x := -total_w * 0.5
	if marker_w > 0.0:
		x += marker_w + GAP
	for i in groups.size():
		if groups[i][0] == owner:
			return Rect2(Vector2(x, 0.0), sizes[i])
		x += sizes[i].x + GAP
	return Rect2()


## A soft gold glow that breathes around the player's movable badge groups, at every zoom.
class _PulseLayer extends Node2D:
	const PERIOD := 2.4  # seconds per breath

	var units: MapUnits

	func _ready() -> void:
		set_process(false)

	func _process(_delta: float) -> void:
		queue_redraw()

	func _draw() -> void:
		if units == null or units.pulse_spaces.is_empty():
			return
		var breath := 0.5 + 0.5 * sin(float(Time.get_ticks_msec()) / 1000.0 * TAU / PERIOD)
		var owner := GameStore.move_faction()
		var inv := units._badge_scale() / units.zoom
		for copy in [-1, 0, 1]:
			for tid in units.pulse_spaces:
				var rect := units.group_rect(int(tid), owner)
				if rect.size == Vector2.ZERO:
					continue
				var anchor: Vector2 = GameData.label_points[int(tid)] + Vector2(copy * GameData.map_w, 0)
				draw_set_transform(anchor + Vector2(0, 11.0 / units.zoom), 0.0, Vector2(inv, inv))
				var glow := rect.grow(2.5)
				draw_rect(glow, Color(1.0, 0.9, 0.5, 0.05 + 0.14 * breath))
				draw_rect(glow, Color(1.0, 0.9, 0.5, 0.25 + 0.5 * breath), false, 1.5)


func _style() -> int:
	if forced_style >= 0:
		return forced_style
	return Style.STRIP if zoom >= STRIP_MIN_ZOOM else Style.FLAG


func set_zoom(z: float) -> void:
	# Badges are constant on-screen size, so their world-space geometry
	# depends on zoom -- redraw on every zoom change.
	zoom = z
	queue_redraw()


func set_style(s: Style) -> void:
	forced_style = s
	queue_redraw()


func _draw() -> void:
	var inv := _badge_scale() / zoom
	for copy in [-1, 0, 1]:
		for tid in GameData.territories:
			var groups := _groups(GameStore.stacks(tid))
			var queued := _groups(GameStore.pending(tid))
			var marker_w := _marker_width(tid)
			if groups.is_empty() and queued.is_empty() and marker_w == 0.0:
				continue
			var anchor: Vector2 = GameData.label_points[tid] + Vector2(copy * GameData.map_w, 0)
			draw_set_transform(anchor + Vector2(0, 11.0 / zoom), 0.0, Vector2(inv, inv))
			_draw_row(tid, groups, 0.0, false, marker_w)
			# Purchases not yet deployed: a second, darker box under the units.
			if not queued.is_empty():
				var y := 0.0 if groups.is_empty() and marker_w == 0.0 else ROW_H + ROW_GAP
				_draw_row(tid, queued, y, true, 0.0)


## [[owner, {unit_type: count}], ...] in stable faction order.
func _groups(by_owner: Dictionary) -> Array:
	var out: Array = []
	for code in GameData.faction_order:
		if by_owner.has(code):
			out.append([code, by_owner[code]])
	return out


## One centred row of an optional value marker followed by faction badges.
func _draw_row(tid: int, groups: Array, y: float, dark: bool, marker_w: float) -> void:
	var sizes: Array = []
	var total_w := marker_w
	for g in groups:
		var sz := _group_size(g[1])
		sizes.append(sz)
		total_w += sz.x + (GAP if total_w > 0.0 else 0.0)
	var x := -total_w * 0.5
	if marker_w > 0.0:
		_draw_value_marker(tid, Vector2(x + marker_w * 0.5, y + 10))
		x += marker_w + GAP
	for i in groups.size():
		_draw_group(Vector2(x, y), groups[i][0], groups[i][1], sizes[i], dark)
		x += sizes[i].x + GAP


## Zoomed in (Strip) the badges are drawn twice as large as the world-zoom
## Flag badges, since a space is roomy enough by then.
func _badge_scale() -> float:
	return STRIP_SCALE if _style() == Style.STRIP else 1.0


## Every land space with a value gets a marker, at every zoom level. 0 = no marker.
func _marker_width(tid: int) -> float:
	var t: Dictionary = GameData.territories[tid]
	if t["type"] != "land":
		return 0.0
	if GameStore.is_neutral(GameStore.owner_of(tid)):
		return 0.0  # neutrals take no part in the game; their value isn't shown
	if GameStore.display_value(tid) <= 0:
		return 0.0
	return STAR_W if GameStore.is_sc(tid) else DISC_W


## The territory's value (Strategic Center bonus included) in a disc of its owner's colour (cream for
## neutrals); a Strategic Center's disc sits inside a gold star.
func _draw_value_marker(tid: int, c: Vector2) -> void:
	var owner := GameStore.owner_of(tid)
	var col := GameStore.display_color(owner) if GameData.factions.has(owner) else Color(0.5, 0.5, 0.5)
	var text_col := Color(0.15, 0.12, 0.05) if GameStore.is_neutral(owner) else Color.WHITE
	var r := DISC_R
	if GameStore.is_sc(tid):
		var pts := HudStyle.star_points(c, STAR_R)
		draw_colored_polygon(pts, Color(1.0, 0.82, 0.25))
		pts.append(pts[0])
		draw_polyline(pts, Color(0, 0, 0, 0.85), 1.5)
		r = STAR_DISC_R
	draw_circle(c, r + 1.0, Color(0, 0, 0, 0.85))
	draw_circle(c, r, col)
	var font := ThemeDB.fallback_font
	var text := str(GameStore.display_value(tid))  # income/deploy value: SC bonus included
	var at := Vector2(c.x - r, c.y + 3.6)
	if text_col == Color.WHITE:
		draw_string_outline(font, at, text, HORIZONTAL_ALIGNMENT_CENTER, r * 2.0, 10, 3, Color(0, 0, 0, 0.9))
	draw_string(font, at, text, HORIZONTAL_ALIGNMENT_CENTER, r * 2.0, 10, text_col)


func _sorted_types(by_type: Dictionary) -> Array:
	var types: Array = by_type.keys()
	types.sort_custom(func(a, b):
		if by_type[a] != by_type[b]:
			return by_type[a] > by_type[b]
		if GameStore.base_type(a) == GameStore.base_type(b):
			return GameStore.rank_of(a) > GameStore.rank_of(b)  # the most promoted stack first
		return _unit_cost(a) > _unit_cost(b))
	return types


func _unit_cost(unit_type: String) -> float:
	var u = GameData.units["units"].get(GameStore.base_type(unit_type))
	if u == null or u.get("cost") == null:
		return 0.0
	return float(u["cost"])


func _by_category(by_type: Dictionary) -> Dictionary:
	var out := {}
	for t in by_type:
		var cat: String = GameData.units["units"][GameStore.base_type(t)]["category"]
		out[cat] = out.get(cat, 0) + by_type[t]
	return out


func _group_size(by_type: Dictionary) -> Vector2:
	match _style():
		Style.FLAG:
			return Vector2(40, 20)
		Style.STRIP:
			return Vector2(6 + 27 * by_type.size(), 20)
		_:
			return Vector2(6 + 27 * _by_category(by_type).size(), 20)


func _draw_group(pos: Vector2, owner: String, by_type: Dictionary, size: Vector2, dark := false) -> void:
	var col: Color = GameData.factions[owner].color
	# Queued purchases get a white border to set them apart from units on the board.
	draw_rect(Rect2(pos - Vector2(1.5, 1.5), size + Vector2(3, 3)), Color(1, 1, 1, 0.95) if dark else Color(0, 0, 0, 0.85))
	draw_rect(Rect2(pos, size), col.darkened(0.5) if dark else col.lightened(0.05))
	match _style():
		Style.FLAG:
			# One icon for the group: the most numerous unit type (promoted and
			# not merged), starred if any unit of that type is promoted.
			var merged := {}
			var starred := {}  # base type -> its highest promotion rank among the stacks
			var total := 0
			for k in by_type:
				var b := GameStore.base_type(k)
				merged[b] = int(merged.get(b, 0)) + by_type[k]
				starred[b] = maxi(int(starred.get(b, 0)), GameStore.rank_of(k))
				total += by_type[k]
			var top: String = _sorted_types(merged)[0]
			_glyph(top, pos + Vector2(4, 3), 14)
			if int(starred[top]) > 0:
				_promotion_star(pos + Vector2(4 + 14, 3), 4.5, int(starred[top]))
			_count(str(total), pos + Vector2(22, 15), FONT_SIZE)
		Style.STRIP:
			var x := pos.x + 3
			for t in _sorted_types(by_type):
				_glyph(GameStore.base_type(t), Vector2(x, pos.y + 3), 14)
				if GameStore.is_promoted(t):
					_promotion_star(Vector2(x + 7, pos.y + 1), 5.0, GameStore.rank_of(t))
				_count(str(by_type[t]), Vector2(x + 15, pos.y + 15), 10)
				x += 27
		Style.CATEGORY:
			var cats := _by_category(by_type)
			var x := pos.x + 3
			for cat in ["Land", "Sea", "Air"]:
				if cats.has(cat):
					_glyph(CATEGORY_ICON[cat], Vector2(x, pos.y + 3), 14)
					_count(str(cats[cat]), Vector2(x + 15, pos.y + 15), 10)
					x += 27


## The gold star marking a promoted unit, centred on `c`; from the second promotion on
## it carries the number of promotions.
func _promotion_star(c: Vector2, r: float, rank := 1) -> void:
	var pts := HudStyle.star_points(c, r if rank < 2 else r * 1.5)
	draw_colored_polygon(pts, Color(1.0, 0.82, 0.25))
	pts.append(pts[0])
	draw_polyline(pts, Color(0, 0, 0, 0.9), 1.0)
	if rank >= 2:
		var font := ThemeDB.fallback_font
		var text := str(rank)
		draw_string(font, c + Vector2(-r * 1.5, r * 0.55), text, HORIZONTAL_ALIGNMENT_CENTER, r * 3.0, 9, Color(0.1, 0.06, 0.0))


func _glyph(unit_type: String, p: Vector2, px: float) -> void:
	var tex := UnitIcons.get_icon(unit_type)
	if tex != null:
		draw_texture_rect(tex, Rect2(p, Vector2(px, px)), false)


func _count(text: String, baseline: Vector2, size: int) -> void:
	var font := ThemeDB.fallback_font
	draw_string_outline(font, baseline, text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, 3, Color(0, 0, 0, 0.9))
	draw_string(font, baseline, text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color.WHITE)
