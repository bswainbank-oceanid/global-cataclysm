extends Node
## Read-only reference data (autoload "GameData"): territories, polygon
## shapes, factions, units. Loaded once from res://data/, which
## tools/sync_client_data.py fills from the repo's data/ dir.

var map_w := 3500.0
var map_h := 1958.0

var territories := {}   # int id -> {id, type, name, x, y, faction?, value?, strategic_center?}
var land_ids: Array[int] = []
var sea_ids: Array[int] = []
var shapes := {}        # int id -> Array[PackedVector2Array], as extracted (used for hit-testing/outlines)
var fill_shapes := {}   # int id -> Array[PackedVector2Array], cleaned so draw_colored_polygon can triangulate them
var bboxes := {}        # int id -> Rect2 covering every polygon of that territory
var label_points := {}  # int id -> Vector2, a point guaranteed inside the largest polygon
var factions := {}      # code -> {name, color: Color}
var faction_order: Array[String] = []
var units := {}         # unit type name -> stats dict


func _ready() -> void:
	_load_territories()
	_load_shapes()
	_load_factions()
	units = _read_json("res://data/units.json")
	print("[GameData] %d land, %d sea, %d factions" % [land_ids.size(), sea_ids.size(), factions.size()])


func _read_json(path: String) -> Variant:
	var text := FileAccess.get_file_as_string(path)
	var parsed = JSON.parse_string(text)
	if parsed == null:
		push_error("could not parse %s (run tools/sync_client_data.py?)" % path)
	return parsed


func _load_territories() -> void:
	var d: Dictionary = _read_json("res://data/territories.json")
	map_w = float(d["reference_image_width_px"])
	map_h = float(d["reference_image_height_px"])
	for s in d["spaces"]:
		var tid := int(s["id"])
		territories[tid] = s
		if s["type"] == "land":
			land_ids.append(tid)
		else:
			sea_ids.append(tid)


func _load_shapes() -> void:
	var d: Dictionary = _read_json("res://data/territory_shapes.json")
	for key in d["shapes"]:
		var tid := int(key)
		var polys: Array[PackedVector2Array] = []
		var box := Rect2()
		var first := true
		for raw in d["shapes"][key]:
			var poly := PackedVector2Array()
			for p in raw:
				poly.append(Vector2(p[0], p[1]))
			polys.append(poly)
			for p in poly:
				if first:
					box = Rect2(p, Vector2.ZERO)
					first = false
				else:
					box = box.expand(p)
		shapes[tid] = polys
		fill_shapes[tid] = _fillable(tid, polys)
		bboxes[tid] = box
		label_points[tid] = _label_point(polys)
	for tid in sea_ids:
		label_points[tid] = _sea_label_point(tid)


## Polygons Godot's triangulator rejects (self-touching outlines from the
## flood-fill extraction) are re-derived as simple polygons via a boolean
## union, which resolves the self-intersections. Anything still unusable is
## logged rather than silently left unpainted.
func _fillable(tid: int, polys: Array[PackedVector2Array]) -> Array[PackedVector2Array]:
	var out: Array[PackedVector2Array] = []
	for poly in polys:
		if not Geometry2D.triangulate_polygon(poly).is_empty():
			out.append(poly)
			continue
		var fixed_any := false
		for cleaned in Geometry2D.merge_polygons(poly, PackedVector2Array()):
			if Geometry2D.is_polygon_clockwise(cleaned):
				continue  # a hole in the union, not a fillable outline
			if not Geometry2D.triangulate_polygon(cleaned).is_empty():
				out.append(cleaned)
				fixed_any = true
		if not fixed_any:
			push_warning("territory %d: polygon of %d pts could not be made fillable" % [tid, poly.size()])
	return out


func _load_factions() -> void:
	var d: Dictionary = _read_json("res://data/factions.json")
	for code in d["factions"]:
		factions[code] = {
			"name": d["factions"][code]["name"],
			"color": Color(d["factions"][code]["color"]),
		}
		faction_order.append(code)


func _polygon_area_centroid(poly: PackedVector2Array) -> Array:
	var a := 0.0
	var cx := 0.0
	var cy := 0.0
	for i in poly.size():
		var p := poly[i]
		var q := poly[(i + 1) % poly.size()]
		var cross := p.x * q.y - q.x * p.y
		a += cross
		cx += (p.x + q.x) * cross
		cy += (p.y + q.y) * cross
	a *= 0.5
	if absf(a) < 0.001:
		return [0.0, poly[0]]
	return [absf(a), Vector2(cx, cy) / (6.0 * a)]


## Centroid of the largest polygon, or -- for concave shapes whose centroid
## falls outside -- the inside grid sample nearest to it.
func _label_point(polys: Array[PackedVector2Array]) -> Vector2:
	var best_poly := polys[0]
	var best_area := -1.0
	var best_centroid := Vector2.ZERO
	for poly in polys:
		var ac := _polygon_area_centroid(poly)
		if ac[0] > best_area:
			best_area = ac[0]
			best_poly = poly
			best_centroid = ac[1]
	if Geometry2D.is_point_in_polygon(best_centroid, best_poly):
		return best_centroid
	var box := Rect2(best_poly[0], Vector2.ZERO)
	for p in best_poly:
		box = box.expand(p)
	var best := best_poly[0]
	var best_d := INF
	for iy in range(1, 16):
		for ix in range(1, 16):
			var s := box.position + Vector2(box.size.x * ix / 16.0, box.size.y * iy / 16.0)
			if Geometry2D.is_point_in_polygon(s, best_poly):
				var dist := s.distance_squared_to(best_centroid)
				if dist < best_d:
					best_d = dist
					best = s
	return best


## A sea zone's polygon is its outer boundary only, so its centroid can sit
## on a coast or island it encloses. Pick the grid sample inside the zone,
## not over any land, nearest the centroid.
func _sea_label_point(tid: int) -> Vector2:
	var fallback: Vector2 = label_points[tid]
	var box: Rect2 = bboxes[tid]
	var best := fallback
	var best_d := INF
	var found := false
	for iy in range(1, 24):
		for ix in range(1, 24):
			var sp := box.position + Vector2(box.size.x * ix / 24.0, box.size.y * iy / 24.0)
			var over_land := false
			for lid in land_ids:
				if bboxes[lid].has_point(sp):
					for poly in shapes[lid]:
						if Geometry2D.is_point_in_polygon(sp, poly):
							over_land = true
							break
				if over_land:
					break
			if over_land:
				continue
			var in_sea := false
			for poly in shapes[tid]:
				if Geometry2D.is_point_in_polygon(sp, poly):
					in_sea = true
					break
			if in_sea and sp.distance_squared_to(fallback) < best_d:
				best_d = sp.distance_squared_to(fallback)
				best = sp
				found = true
	return best if found else fallback


## The id of the space under `p` (map pixel coords, already wrapped into
## [0, map_w)), land before sea -- a sea zone's polygon is its outer
## boundary only, so it overlaps any island/coast it encloses. -1 if none.
func space_at(p: Vector2) -> int:
	for id_list in [land_ids, sea_ids]:
		for tid in id_list:
			if not bboxes[tid].has_point(p):
				continue
			for poly in shapes[tid]:
				if Geometry2D.is_point_in_polygon(p, poly):
					return tid
	return -1
