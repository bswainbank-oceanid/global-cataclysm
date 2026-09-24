class_name UnitTile
extends Button
## One unit in the selection panel: its icon on the owner's colour, with
##   - gold stars above it, one per promotion (up to four; beyond that one bigger
##     star carrying the number),
##   - XP pips beneath it (one per XP toward the next promotion),
##   - a column of HP boxes on its right: white for HP left, red for HP lost (two columns
##     once there are more than six, and the numbers "left/max" written under the unit
##     from five up, so the HP left can always be read).
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
const HP_TWO_COLUMNS := 6   # more HP than this is drawn in two columns
const HP_TEXT_FROM := 5     # this much max HP or more also gets the "left/max" text
const XP_PIPS := 5  # rules.json promotion.xp_required
const DEFAULT_MAX_PROMOTIONS := 3  # rules.json promotion.max_promotions (units.json may give a type its own: Infantry 5)
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
var _shake_x := 0.0        # the battle board's reactions: a roll shakes the unit side to side,
var _bounce_y := 0.0       # and a hit bounces it up and down (drawn as an offset; the layout is untouched)
var _shake_tween: Tween
var _bounce_tween: Tween


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
		"current_hp": row["hp"], "xp": row["xp"], "promoted": row["promoted"], "promotions": row["promotions"],
		"max_promotions": row.get("max_promotions"),
	}
	var tile := UnitTile.make(u, bool(row["cargo"]))
	tile.max_hp_override = int(row["max_hp"])
	return tile


## Refresh from a BattleModel unit row (HP, XP, promotion, mark).
func update_from_battle(row: Dictionary) -> void:
	unit["current_hp"] = row["hp"]
	unit["xp"] = row["xp"]
	unit["promoted"] = row["promoted"]
	unit["promotions"] = row["promotions"]
	max_hp_override = int(row["max_hp"])
	mark = int(row["mark"])
	tooltip_text = _describe()
	queue_redraw()


## The most promotions this unit can earn (a unit at that rank earns no more XP).
func max_rank() -> int:
	if unit.get("max_promotions") != null:
		return int(unit["max_promotions"])
	return int(GameData.units["units"].get(unit["unit_type"], {}).get("max_promotions", DEFAULT_MAX_PROMOTIONS))


func max_hp() -> int:
	if max_hp_override >= 0:
		return max_hp_override
	var base := int(GameData.units["units"][unit["unit_type"]]["hp"])
	return base + int(unit.get("promotions", 0))


func _describe() -> String:
	var line := "%s #%d\nHP %d/%d   XP %d/%d%s" % [
		unit["unit_type"], int(unit["unit_id"]), int(unit["current_hp"]), max_hp(),
		mini(int(unit.get("xp", 0)), XP_PIPS), XP_PIPS, "   (%d promotion%s%s)" % [int(unit.get("promotions", 0)), "" if int(unit.get("promotions", 0)) == 1 else "s", ", top rank" if int(unit.get("promotions", 0)) >= max_rank() else ""] if int(unit.get("promotions", 0)) > 0 else ""]
	return "Transport carrying " + line if in_transport else line


## A quick side-to-side shake, after `delay` seconds: this unit is rolling.
func shake(delay := 0.0) -> void:
	if _shake_tween != null:
		_shake_tween.kill()
	_shake_tween = create_tween()
	if delay > 0.0:
		_shake_tween.tween_interval(delay)
	_shake_tween.tween_method(func(t: float):
		_shake_x = sin(t * TAU * 3.0) * 2.5 * (1.0 - t)
		queue_redraw(), 0.0, 1.0, 0.32)
	_shake_tween.tween_callback(func():
		_shake_x = 0.0
		queue_redraw())


## A bounce up and down, after `delay` seconds: this unit was hit.
func bounce(delay := 0.0) -> void:
	if _bounce_tween != null:
		_bounce_tween.kill()
	_bounce_tween = create_tween()
	if delay > 0.0:
		_bounce_tween.tween_interval(delay)
	_bounce_tween.tween_method(func(t: float):
		_bounce_y = -absf(sin(t * TAU)) * 7.0 * (1.0 - t)
		queue_redraw(), 0.0, 1.0, 0.45)
	_bounce_tween.tween_callback(func():
		_bounce_y = 0.0
		queue_redraw())


func _draw() -> void:
	draw_set_transform(Vector2(_shake_x, _bounce_y), 0.0, Vector2.ONE)
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
		GameStore.tile_drag_kind = str(drag_payload.get("kind", ""))


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


func _draw_star(c: Vector2, r: float) -> void:
	var pts := HudStyle.star_points(c, r)
	draw_colored_polygon(pts, Color(1.0, 0.82, 0.25))
	pts.append(pts[0])
	draw_polyline(pts, Color(0, 0, 0, 0.9), 1.0)


## The unit with everything about it: icon, promotion star above, XP pips
## below, HP boxes at the right.
func _draw_package(offset: Vector2, owner_col: Color) -> void:
	_draw_icon(unit["unit_type"], offset, owner_col)

	# Promotion stars above the unit: one each up to four, then one bigger star with the number.
	var ranks := int(unit.get("promotions", 0))
	if ranks > 0:
		var cx := offset.x + ICON_POS.x + ICON * 0.5
		if ranks <= 4:
			var r := 6.0 if ranks == 1 else 4.6
			var step := r * 2.0 - 0.5
			for i in ranks:
				_draw_star(offset + Vector2(cx - offset.x + (i - (ranks - 1) * 0.5) * step, 6.5), r)
		else:
			_draw_star(offset + Vector2(cx - offset.x, 6.5), 7.0)
			draw_string(ThemeDB.fallback_font, offset + Vector2(cx - offset.x - 10.0, 9.6), str(ranks), HORIZONTAL_ALIGNMENT_CENTER, 20.0, 9, Color(0.1, 0.06, 0.0))

	# XP pips under the unit (a top-rank unit has nothing left to earn: all gold).
	var xp := XP_PIPS if ranks >= max_rank() else mini(int(unit.get("xp", 0)), XP_PIPS)
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

	# HP boxes, right of the icon, filling up from the bottom; red = HP lost. A tall stack
	# (more than HP_TWO_COLUMNS) is split into two columns so each box stays big enough to read.
	var total := max_hp()
	var hp := clampi(int(unit["current_hp"]), 0, total)
	var columns := 2 if total > HP_TWO_COLUMNS else 1
	var rows := ceili(float(total) / columns)
	var box_gap := 2.0 if rows <= 5 else 1.0
	var box_h := (ICON - box_gap * (rows - 1)) / rows
	var box_w := HP_W if columns == 1 else 6.0
	var x0 := HP_X if columns == 1 else 37.5
	for i in total:  # i = 0 is the bottom box of the first column
		var col := i / rows
		var row := i % rows
		var y := offset.y + ICON_POS.y + ICON - (row + 1) * box_h - row * box_gap
		var r := Rect2(offset.x + x0 + col * (box_w + 1.0), y, box_w, box_h)
		draw_rect(r, Color.WHITE if i < hp else Color(0.9, 0.15, 0.15))
		if box_h >= 4.0:
			draw_rect(r, Color(0, 0, 0, 0.85), false, 1.0)
	if total >= HP_TEXT_FROM:
		var text := "%d/%d" % [hp, total]
		var at := offset + Vector2(ICON_POS.x - 1.0, 62.0)
		draw_string_outline(ThemeDB.fallback_font, at, text, HORIZONTAL_ALIGNMENT_LEFT, -1, 9, 3, Color(0, 0, 0, 0.9))
		draw_string(ThemeDB.fallback_font, at, text, HORIZONTAL_ALIGNMENT_LEFT, -1, 9, Color.WHITE if hp > 0 else Color(1.0, 0.4, 0.35))
