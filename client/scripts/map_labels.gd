class_name MapLabels
extends Node2D
## Territory labels, drawn at constant on-screen size (the transform is
## counter-scaled by 1/zoom). Compact ids only when zoomed out, id + name
## when zoomed in, sea zones dimmer and only once zoomed in a bit.

const LAND_FULL_NAME_ZOOM := 0.75
const SEA_MIN_ZOOM := 0.55
const FONT_SIZE := 12

var zoom := 1.0


func set_zoom(z: float) -> void:
	# Redraw only when a label-visibility threshold is crossed, not on every
	# frame of a smooth zoom.
	var old_bucket := _bucket(zoom)
	zoom = z
	if _bucket(z) != old_bucket:
		queue_redraw()


func _bucket(z: float) -> int:
	return int(z >= LAND_FULL_NAME_ZOOM) + int(z >= SEA_MIN_ZOOM) * 2


func _draw() -> void:
	var font := ThemeDB.fallback_font
	var inv := 1.0 / zoom
	for copy in [-1, 0, 1]:
		for tid in GameData.territories:
			var t: Dictionary = GameData.territories[tid]
			var is_sea: bool = t["type"] == "sea"
			if is_sea and zoom < SEA_MIN_ZOOM:
				continue
			var text := str(tid)
			if zoom >= LAND_FULL_NAME_ZOOM:
				text = "%d. %s" % [tid, t["name"]]
			var p: Vector2 = GameData.label_points[tid] + Vector2(copy * GameData.map_w, 0)
			draw_set_transform(p, 0.0, Vector2(inv, inv))
			var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x
			var pos := Vector2(-w * 0.5, FONT_SIZE * 0.35)
			var fill := Color(0.02, 0.2, 0.33, 0.95) if is_sea else Color(1, 1, 1, 0.97)
			var outline := Color(0.86, 0.97, 1.0, 0.9) if is_sea else Color(0, 0, 0, 0.9)
			draw_string_outline(font, pos, text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, 3, outline)
			draw_string(font, pos, text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, fill)
