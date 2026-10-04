class_name MapLabels
extends Node2D
## Territory labels, drawn at constant on-screen size (the transform is
## counter-scaled by 1/zoom). Compact ids only when zoomed out, id + name
## when zoomed in, sea zones dimmer and only once zoomed in a bit. No labels
## at all at the far zoomed-out world view, and noncombatant powers' land never
## shows its id (just its name, once zoomed in).

const LAND_FULL_NAME_ZOOM := 0.75
const SEA_MIN_ZOOM := 0.55
const LAND_MIN_ZOOM := 0.55
const FONT_SIZE := 13

var zoom := 1.0


func set_zoom(z: float) -> void:
	# Label size is counter-scaled by 1/zoom, so it must be redrawn on every
	# zoom change -- skipping frames between visibility thresholds left the
	# text growing with the map.
	zoom = z
	queue_redraw()


func _draw() -> void:
	var font := GCTheme.font("display")  # the style guide's territory label: condensed capitals
	var inv := 1.0 / zoom
	for copy in [-1, 0, 1]:
		for tid in GameData.territories:
			var t: Dictionary = GameData.territories[tid]
			var is_sea: bool = t["type"] == "sea"
			if is_sea and zoom < SEA_MIN_ZOOM:
				continue
			if not is_sea and zoom < LAND_MIN_ZOOM:
				continue
			var noncombatant := not is_sea and GameStore.is_noncombatant(GameStore.owner_of(tid))
			if noncombatant and zoom < LAND_FULL_NAME_ZOOM:
				continue
			var text := str(tid)
			if zoom >= LAND_FULL_NAME_ZOOM:
				text = (str(t["name"]) if noncombatant else "%d. %s" % [tid, t["name"]]).to_upper()
			var p: Vector2 = GameData.label_points[tid] + Vector2(copy * GameData.map_w, 0)
			draw_set_transform(p, 0.0, Vector2(inv, inv))
			var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x
			var pos := Vector2(-w * 0.5, FONT_SIZE * 0.35)
			# land: white on a navy edge and a hard 1px navy shadow; sea: navy on a cream edge
			var fill := Color(GCTheme.NAVY, 0.95) if is_sea else GCTheme.WHITE
			var outline := Color(GCTheme.CREAM, 0.85) if is_sea else Color(GCTheme.NAVY_DARK, 0.9)
			if not is_sea:
				draw_string_outline(font, pos + Vector2(1, 1), text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, 3, GCTheme.NAVY_DARK)
			draw_string_outline(font, pos, text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, 3, outline)
			draw_string(font, pos, text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, fill)
