class_name MapTargets
extends Node2D
## Highlights the spaces the selected units can move to. Land targets get a
## translucent green fill; a sea zone's polygon is only its outer boundary and
## encloses the coastal land, so sea fills go in a layer BENEATH the ownership
## fill (Pass.UNDER_LAND), like the sea highlight. Every target also gets a green
## outline (Pass.TOP); the one under the cursor while dragging is brighter.

enum Pass { TOP, UNDER_LAND }

var pass_kind := Pass.TOP
var targets: Array = []
var hovered := -1
var zoom := 1.0


func set_zoom(z: float) -> void:
	zoom = z
	queue_redraw()


func set_targets(ids: Array, hover: int) -> void:
	targets = ids
	hovered = hover
	queue_redraw()


func _draw() -> void:
	for copy in [-1, 0, 1]:
		draw_set_transform(Vector2(copy * GameData.map_w, 0), 0.0, Vector2.ONE)
		for tid in targets:
			var is_sea: bool = GameData.territories[tid]["type"] == "sea"
			var hot: bool = tid == hovered
			var fill := Color(0.3, 1.0, 0.45, 0.42 if hot else 0.22)
			if pass_kind == Pass.UNDER_LAND:
				if is_sea:
					for fpoly in GameData.fill_shapes[tid]:
						draw_colored_polygon(fpoly, fill)
				continue
			if not is_sea:
				for fpoly in GameData.fill_shapes[tid]:
					draw_colored_polygon(fpoly, fill)
			var line := Color(0.5, 1.0, 0.6, 1.0 if hot else 0.85)
			for poly in GameData.shapes[tid]:
				var loop := PackedVector2Array(poly)
				loop.append(poly[0])
				draw_polyline(loop, line, (5.0 if hot else 3.0) / zoom, true)
