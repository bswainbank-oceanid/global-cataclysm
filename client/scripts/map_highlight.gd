class_name MapHighlight
extends Node2D
## Hover and selection outlines. Line widths are counter-scaled by 1/zoom so
## they stay a constant number of screen pixels at every zoom level.

enum Pass { TOP, UNDER_LAND }
## UNDER_LAND draws only SEA-zone fills, meant to sit beneath the ownership
## fill so land the zone's outer-boundary polygon encloses stays untinted;
## TOP draws every outline, plus land fills.
var pass_kind := Pass.TOP
var hovered := -1
var selected := -1
var zoom := 1.0


func set_zoom(z: float) -> void:
	zoom = z
	queue_redraw()


func set_hovered(tid: int) -> void:
	if tid != hovered:
		hovered = tid
		queue_redraw()


func set_selected(tid: int) -> void:
	if tid != selected:
		selected = tid
		queue_redraw()


func _draw() -> void:
	for copy in [-1, 0, 1]:
		draw_set_transform(Vector2(copy * GameData.map_w, 0), 0.0, Vector2.ONE)
		if hovered >= 0 and hovered != selected:
			_outline(hovered, Color(1, 1, 1, 0.95), 2.0, Color(1, 1, 1, 0.13))
		if selected >= 0:
			_outline(selected, Color(1.0, 0.82, 0.25, 1.0), 3.5, Color(1.0, 0.82, 0.25, 0.16))


func _outline(tid: int, line: Color, width_px: float, fill: Color) -> void:
	var is_sea: bool = GameData.territories[tid]["type"] == "sea"
	for poly in GameData.shapes[tid]:
		if pass_kind == Pass.UNDER_LAND:
			if is_sea:
				draw_colored_polygon(poly, fill)
			continue
		if not is_sea:
			draw_colored_polygon(poly, fill)
		var loop := PackedVector2Array(poly)
		loop.append(poly[0])
		draw_polyline(loop, line, width_px / zoom, true)
