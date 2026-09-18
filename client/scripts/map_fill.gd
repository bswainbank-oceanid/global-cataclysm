class_name MapFill
extends Node2D
## Translucent faction-colour fill over every owned LAND territory. Redraws
## only when game state changes (panning/zooming is just the camera moving
## over this cached canvas item). Drawn three times, offset by the map
## width, to match the wrap-around copies of the base map.


func _ready() -> void:
	GameStore.state_changed.connect(queue_redraw)


func _draw() -> void:
	for copy in [-1, 0, 1]:
		draw_set_transform(Vector2(copy * GameData.map_w, 0), 0.0, Vector2.ONE)
		for tid in GameData.land_ids:
			var owner := GameStore.owner_of(tid)
			if owner == "" or not GameData.factions.has(owner):
				continue
			var col: Color = GameData.factions[owner].color
			col.a = 0.7
			for poly in GameData.fill_shapes[tid]:
				draw_colored_polygon(poly, col)
