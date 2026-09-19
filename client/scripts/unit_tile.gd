class_name UnitTile
extends Button
## One unit in the selection panel: its icon on the owner's colour, with
##   - a gold star above it if promoted,
##   - XP pips beneath it (one per XP toward the promotion threshold),
##   - a column of HP boxes on its right: white for HP left, red for HP lost.
## The tile itself is an invisible toggle button -- nothing is drawn for it
## until it is hovered (thin outline) or selected (gold outline) -- so a set
## of units can be picked for orders later.
##
## A land unit at sea is in transport form: its tile is about twice as wide,
## a regular-size Transport icon with the carried unit's full package (star,
## XP, HP as above) beside it in a bordered box. The whole pair is one tile,
## selecting the carried unit.

const SIZE := Vector2(52, 66)
const TRANSPORT_SIZE := Vector2(104, 66)
const ICON := 32.0
const ICON_POS := Vector2(3, 13)
const HP_X := 40.0
const HP_W := 7.0
const XP_PIPS := 5  # rules.json promotion.xp_required
const CARGO_BOX := Rect2(43, 1, 58, 64)   # the bordered box around the carried unit (transport form)
const CARGO_OFFSET := Vector2(46, 2)      # where that unit's package starts inside it

var unit: Dictionary
var in_transport := false  # a land unit at sea: drawn as a Transport carrying it
var max_hp_override := -1  # the battle board: HP as the battle counts it (a Transport has 1)
var mark := 0              # BattleModel.Mark: 1 = hit but alive ("/"), 2 = eliminated ("X")
var dimmed := false        # can't be picked right now (no legal move, or already committed)
var committed := false     # queued to move: drawn with an arrow badge; clicking recalls it
var drag_payload := {}     # non-empty: this tile can start a move drag carrying this payload
var rolling := false       # the battle board: this unit's roll is the one on show


static func make(u: Dictionary, transport_form := false) -> UnitTile:
	var tile := UnitTile.new()
	tile.unit = u
	tile.in_transport = transport_form
	tile.custom_minimum_size = TRANSPORT_SIZE if transport_form else SIZE
	tile.toggle_mode = true
	tile.focus_mode = Control.FOCUS_NONE
	tile.tooltip_text = tile._describe()
	tile.button_down.connect(tile._arm_drag)
	var invisible := StyleBoxEmpty.new()
	for state in ["normal", "pressed", "hover", "hover_pressed", "focus", "disabled"]:
		tile.add_theme_stylebox_override(state, invisible)
	return tile


## A tile for a unit as the battle board tracks it (a BattleModel unit row).
static func for_battle(row: Dictionary) -> UnitTile:
	var u := {
		"unit_id": row["unit_id"], "unit_type": row["unit_type"], "owner": row["owner"],
		"current_hp": row["hp"], "xp": row["xp"], "promoted": row["promoted"],
	}
	var tile := UnitTile.make(u, bool(row["cargo"]))
	tile.max_hp_override = int(row["max_hp"])
	return tile


## Refresh from a BattleModel unit row (HP, XP, promotion, mark).
func update_from_battle(row: Dictionary) -> void:
	unit["current_hp"] = row["hp"]
	unit["xp"] = row["xp"]
	unit["promoted"] = row["promoted"]
	max_hp_override = int(row["max_hp"])
	mark = int(row["mark"])
	tooltip_text = _describe()
	queue_redraw()


func max_hp() -> int:
	if max_hp_override >= 0:
		return max_hp_override
	var base := int(GameData.units["units"][unit["unit_type"]]["hp"])
	return base + (1 if unit.get("promoted", false) else 0)


func _describe() -> String:
	var line := "%s #%d\nHP %d/%d   XP %d/%d%s" % [
		unit["unit_type"], int(unit["unit_id"]), int(unit["current_hp"]), max_hp(),
		mini(int(unit.get("xp", 0)), XP_PIPS), XP_PIPS, "   (promoted)" if unit.get("promoted", false) else ""]
	return "Transport carrying " + line if in_transport else line


func _draw() -> void:
	var owner_col: Color = GameData.factions[unit["owner"]].color
	if button_pressed:
		draw_rect(Rect2(Vector2.ZERO, size), Color(1.0, 0.82, 0.25, 0.14))
		draw_rect(Rect2(Vector2.ZERO, size), Color(1.0, 0.82, 0.25), false, 2.0)
	elif is_hovered():
		draw_rect(Rect2(Vector2.ZERO, size), Color(1, 1, 1, 0.5), false, 1.0)
	if rolling:
		draw_rect(Rect2(Vector2.ZERO, size), Color(1.0, 0.9, 0.35, 0.22))
		draw_rect(Rect2(Vector2.ZERO, size).grow(-1.0), Color(1.0, 0.9, 0.35), false, 3.0)

	if not in_transport:
		_draw_package(Vector2.ZERO, owner_col)
	else:
		# The transport itself: a regular-size icon, with no XP or HP of its own.
		_draw_icon("Transport", Vector2.ZERO, owner_col)
		draw_rect(CARGO_BOX, owner_col.darkened(0.6))
		draw_rect(CARGO_BOX, Color(1, 1, 1, 0.9), false, 1.5)
		_draw_package(CARGO_OFFSET, owner_col)
	_draw_mark()
	if dimmed or committed:
		draw_rect(Rect2(ICON_POS - Vector2(2, 2), Vector2(ICON, ICON) + Vector2(4, 4)), Color(0.05, 0.07, 0.1, 0.62))
	if committed:
		# an arrow badge on the icon, and a small x in the corner: click to recall
		var c := ICON_POS + Vector2(ICON, ICON) * 0.5
		draw_line(c + Vector2(-9, 0), c + Vector2(6, 0), Color(1.0, 0.85, 0.3), 3.0)
		draw_colored_polygon(PackedVector2Array([c + Vector2(11, 0), c + Vector2(3, -6), c + Vector2(3, 6)]), Color(1.0, 0.85, 0.3))
		var x0 := ICON_POS + Vector2(ICON - 5, -5)
		draw_line(x0, x0 + Vector2(8, 8), Color(1.0, 0.45, 0.4), 2.0)
		draw_line(x0 + Vector2(8, 0), x0 + Vector2(0, 8), Color(1.0, 0.45, 0.4), 2.0)


## Pressing a selected, movable tile arms a move drag: main.gd watches the mouse from
## there (Godot's built-in drag and drop can't be used, since it is never offered the
## map's SubViewportContainer as a drop target under all conditions) and, on release
## over a green target, queues the whole selection's move.
func _arm_drag() -> void:
	if not drag_payload.is_empty():
		GameStore.tile_drag_armed = true


## Battle marks: "/" through a unit that was hit but lives, "X" through an
## eliminated one.
func _draw_mark() -> void:
	if mark == 0:
		return
	var box := Rect2(Vector2(0, 8), Vector2(size.x, size.y - 12))
	var col := Color(1.0, 0.25, 0.2)
	if mark == 1:
		draw_line(box.position + Vector2(box.size.x, 0), box.position + Vector2(0, box.size.y), Color(0, 0, 0, 0.9), 5.0, true)
		draw_line(box.position + Vector2(box.size.x, 0), box.position + Vector2(0, box.size.y), col, 3.0, true)
	else:
		for pair in [[Vector2(0, 0), Vector2(box.size.x, box.size.y)], [Vector2(box.size.x, 0), Vector2(0, box.size.y)]]:
			draw_line(box.position + pair[0], box.position + pair[1], Color(0, 0, 0, 0.9), 6.0, true)
			draw_line(box.position + pair[0], box.position + pair[1], col, 3.5, true)


## A unit's icon on its owner's colour, at `offset` within the tile.
func _draw_icon(unit_type: String, offset: Vector2, owner_col: Color) -> void:
	var icon_rect := Rect2(ICON_POS + offset, Vector2(ICON, ICON))
	draw_rect(icon_rect.grow(1.5), Color(0, 0, 0, 0.85))
	draw_rect(icon_rect, owner_col.lightened(0.05))
	var tex := UnitIcons.get_icon(unit_type)
	if tex != null:
		draw_texture_rect(tex, icon_rect.grow(-4), false)


## The unit with everything about it: icon, promotion star above, XP pips
## below, HP boxes at the right.
func _draw_package(offset: Vector2, owner_col: Color) -> void:
	_draw_icon(unit["unit_type"], offset, owner_col)

	# Promotion star above the unit.
	if unit.get("promoted", false):
		var pts := HudStyle.star_points(offset + Vector2(ICON_POS.x + ICON * 0.5, 6.5), 6.0)
		draw_colored_polygon(pts, Color(1.0, 0.82, 0.25))
		pts.append(pts[0])
		draw_polyline(pts, Color(0, 0, 0, 0.9), 1.0)

	# XP pips under the unit.
	var xp := mini(int(unit.get("xp", 0)), XP_PIPS)
	var pip := 4.0
	var gap := 2.0
	var row_w := XP_PIPS * pip + (XP_PIPS - 1) * gap
	var px := offset.x + ICON_POS.x + (ICON - row_w) * 0.5
	var py := offset.y + ICON_POS.y + ICON + 6.0
	for i in XP_PIPS:
		var r := Rect2(px + i * (pip + gap), py, pip, pip)
		if i < xp:
			draw_rect(r, Color(1.0, 0.82, 0.25))
		else:
			draw_rect(r, Color(0.5, 0.55, 0.62, 0.35))
		draw_rect(r, Color(0, 0, 0, 0.8), false, 1.0)

	# HP boxes, right of the icon, filling up from the bottom; red = HP lost.
	var total := max_hp()
	var hp := clampi(int(unit["current_hp"]), 0, total)
	var box_gap := 2.0
	var box_h := (ICON - box_gap * (total - 1)) / total
	for i in total:  # i = 0 is the bottom box
		var y := offset.y + ICON_POS.y + ICON - (i + 1) * box_h - i * box_gap
		var r := Rect2(offset.x + HP_X, y, HP_W, box_h)
		draw_rect(r, Color.WHITE if i < hp else Color(0.9, 0.15, 0.15))
		draw_rect(r, Color(0, 0, 0, 0.85), false, 1.0)
