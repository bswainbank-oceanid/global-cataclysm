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

var forced_style := -1  # -1 = adaptive; otherwise a Style, for debug comparisons
var zoom := 1.0


func _ready() -> void:
	GameStore.state_changed.connect(queue_redraw)


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
	var inv := 1.0 / zoom
	for copy in [-1, 0, 1]:
		for tid in GameData.territories:
			var stacks := GameStore.stacks(tid)
			if stacks.is_empty():
				continue
			var groups: Array = []  # [owner, {type: count}] in stable faction order
			for code in GameData.faction_order:
				if stacks.has(code):
					groups.append([code, stacks[code]])
			var sizes: Array = []
			var total_w := 0.0
			for g in groups:
				var sz := _group_size(g[1])
				sizes.append(sz)
				total_w += sz.x
			total_w += 3.0 * (groups.size() - 1)
			var anchor: Vector2 = GameData.label_points[tid] + Vector2(copy * GameData.map_w, 0)
			draw_set_transform(anchor + Vector2(0, 8.0 * inv), 0.0, Vector2(inv, inv))
			var x := -total_w * 0.5
			for i in groups.size():
				_draw_group(Vector2(x, 0), groups[i][0], groups[i][1], sizes[i])
				x += sizes[i].x + 3.0


func _sorted_types(by_type: Dictionary) -> Array:
	var types: Array = by_type.keys()
	types.sort_custom(func(a, b):
		if by_type[a] != by_type[b]:
			return by_type[a] > by_type[b]
		return _unit_cost(a) > _unit_cost(b))
	return types


func _unit_cost(unit_type: String) -> float:
	var u = GameData.units["units"].get(unit_type)
	if u == null or u.get("cost") == null:
		return 0.0
	return float(u["cost"])


func _by_category(by_type: Dictionary) -> Dictionary:
	var out := {}
	for t in by_type:
		var cat: String = GameData.units["units"][t]["category"]
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


func _draw_group(pos: Vector2, owner: String, by_type: Dictionary, size: Vector2) -> void:
	var col: Color = GameData.factions[owner].color
	draw_rect(Rect2(pos - Vector2(1.5, 1.5), size + Vector2(3, 3)), Color(0, 0, 0, 0.85))
	draw_rect(Rect2(pos, size), col.lightened(0.05))
	match _style():
		Style.FLAG:
			var types := _sorted_types(by_type)
			var total := 0
			for t in by_type:
				total += by_type[t]
			_glyph(types[0], pos + Vector2(4, 3), 14)
			_count(str(total), pos + Vector2(22, 15), FONT_SIZE)
		Style.STRIP:
			var x := pos.x + 3
			for t in _sorted_types(by_type):
				_glyph(t, Vector2(x, pos.y + 3), 14)
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


func _glyph(unit_type: String, p: Vector2, px: float) -> void:
	var tex := UnitIcons.get_icon(unit_type)
	if tex != null:
		draw_texture_rect(tex, Rect2(p, Vector2(px, px)), false)


func _count(text: String, baseline: Vector2, size: int) -> void:
	var font := ThemeDB.fallback_font
	draw_string_outline(font, baseline, text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, 3, Color(0, 0, 0, 0.9))
	draw_string(font, baseline, text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color.WHITE)
