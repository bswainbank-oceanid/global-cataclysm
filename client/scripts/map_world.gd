class_name MapWorld
extends Node2D
## Everything that lives in map space: three wrap-around copies of the base
## map, the ownership fill, labels, and hover/selection highlight, plus
## picking (which territory is under the cursor).

signal hovered_changed(tid: int)
signal selected_changed(tid: int)

var fill: MapFill
var arrows: MapArrows
var _targets_under: MapTargets
var _targets_top: MapTargets
var _stripes: Array[MapContested] = []
var units: MapUnits
var labels: MapLabels
var highlight: MapHighlight        # top pass: outlines (+ land fills); the source of truth for hover/selection
var _sea_fill_under: MapHighlight  # sea-zone fills, beneath the ownership fill
var cam: CameraRig  # set by main; hover is derived from its cursor position
var _hover_enabled := false
var _forced_hover := -1


func _ready() -> void:
	var tex := _load_base_texture()
	for copy in [-1, 0, 1]:
		var s := Sprite2D.new()
		s.texture = tex
		s.centered = false
		s.position = Vector2(copy * GameData.map_w, 0)
		s.texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
		add_child(s)
	_sea_fill_under = MapHighlight.new()
	_sea_fill_under.pass_kind = MapHighlight.Pass.UNDER_LAND
	add_child(_sea_fill_under)
	_add_stripes(MapContested.Kind.SEA)  # beneath the ownership fill, like the sea highlight
	_targets_under = MapTargets.new()
	_targets_under.pass_kind = MapTargets.Pass.UNDER_LAND
	add_child(_targets_under)  # sea-zone target fills, beneath the ownership fill
	fill = MapFill.new()
	add_child(fill)
	_add_stripes(MapContested.Kind.LAND)
	_targets_top = MapTargets.new()
	add_child(_targets_top)
	highlight = MapHighlight.new()
	add_child(highlight)
	arrows = MapArrows.new()
	add_child(arrows)
	units = MapUnits.new()
	add_child(units)
	labels = MapLabels.new()  # above the highlight so outlines never cut through text
	add_child(labels)


func _add_stripes(kind: MapContested.Kind) -> void:
	var stripes := MapContested.new()
	stripes.kind = kind
	_stripes.append(stripes)
	add_child(stripes)


## The base map with mipmaps generated at load, so it stays smooth when
## drawn well below native size (a plain import has none by default).
func _load_base_texture() -> Texture2D:
	var img: Image = (load("res://assets/base_map.png") as Texture2D).get_image()
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func set_zoom(z: float) -> void:
	labels.set_zoom(z)
	units.set_zoom(z)
	arrows.set_zoom(z)
	_targets_under.set_zoom(z)
	_targets_top.set_zoom(z)
	highlight.set_zoom(z)
	_sea_fill_under.set_zoom(z)
	for stripes in _stripes:
		stripes.set_zoom(z)


## Highlight where the selected units can move (`hover`: the one under the drag).
func set_move_targets(ids: Array, hover: int) -> void:
	_targets_under.set_targets(ids, hover)
	_targets_top.set_targets(ids, hover)


func set_hover_enabled(on: bool) -> void:
	_hover_enabled = on
	if not on:
		_set_hover(-1)


func force_hover(tid: int) -> void:
	_forced_hover = tid
	_set_hover(tid)


func select(tid: int) -> void:
	if tid != highlight.selected:
		highlight.set_selected(tid)
		_sea_fill_under.set_selected(tid)
		selected_changed.emit(tid)


func space_at_world(world_pos: Vector2) -> int:
	return GameData.space_at(Vector2(fposmod(world_pos.x, GameData.map_w), world_pos.y))


func _process(_delta: float) -> void:
	if _hover_enabled and _forced_hover < 0 and cam != null and cam.mouse_screen.x >= 0.0:
		_set_hover(space_at_world(cam.screen_to_world(cam.mouse_screen)))


func _set_hover(tid: int) -> void:
	if tid != highlight.hovered:
		highlight.set_hovered(tid)
		_sea_fill_under.set_hovered(tid)
		hovered_changed.emit(tid)
