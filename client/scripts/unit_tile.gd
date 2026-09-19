class_name UnitTile
extends Button
## One unit in the selection panel: its icon on the owner's colour, with
##   - a gold star above it if promoted,
##   - XP pips beneath it (one per XP toward the promotion threshold),
##   - a column of HP boxes on its right: white for HP left, red for HP lost.
## The tile itself is an invisible toggle button -- nothing is drawn for it
## until it is hovered (thin outline) or selected (gold outline) -- so a set
## of units can be picked for orders later.

const SIZE := Vector2(52, 66)
const ICON := 32.0
const ICON_POS := Vector2(3, 13)
const HP_X := 40.0
const HP_W := 7.0
const XP_PIPS := 5  # rules.json promotion.xp_required

var unit: Dictionary


static func make(u: Dictionary) -> UnitTile:
	var tile := UnitTile.new()
	tile.unit = u
	tile.custom_minimum_size = SIZE
	tile.toggle_mode = true
	tile.focus_mode = Control.FOCUS_NONE
	tile.tooltip_text = tile._describe()
	var invisible := StyleBoxEmpty.new()
	for state in ["normal", "pressed", "hover", "hover_pressed", "focus", "disabled"]:
		tile.add_theme_stylebox_override(state, invisible)
	return tile


func max_hp() -> int:
	var base := int(GameData.units["units"][unit["unit_type"]]["hp"])
	return base + (1 if unit.get("promoted", false) else 0)


func _describe() -> String:
	return "%s #%d\nHP %d/%d   XP %d/%d%s" % [
		unit["unit_type"], int(unit["unit_id"]), int(unit["current_hp"]), max_hp(),
		mini(int(unit.get("xp", 0)), XP_PIPS), XP_PIPS, "   (promoted)" if unit.get("promoted", false) else ""]


func _draw() -> void:
	var owner_col: Color = GameData.factions[unit["owner"]].color
	if button_pressed:
		draw_rect(Rect2(Vector2.ZERO, size), Color(1.0, 0.82, 0.25, 0.14))
		draw_rect(Rect2(Vector2.ZERO, size), Color(1.0, 0.82, 0.25), false, 2.0)
	elif is_hovered():
		draw_rect(Rect2(Vector2.ZERO, size), Color(1, 1, 1, 0.5), false, 1.0)

	# Icon on the owner's colour.
	var icon_rect := Rect2(ICON_POS, Vector2(ICON, ICON))
	draw_rect(icon_rect.grow(1.5), Color(0, 0, 0, 0.85))
	draw_rect(icon_rect, owner_col.lightened(0.05))
	var tex := UnitIcons.get_icon(unit["unit_type"])
	if tex != null:
		draw_texture_rect(tex, icon_rect.grow(-4), false)

	# Promotion star above the unit.
	if unit.get("promoted", false):
		var pts := HudStyle.star_points(Vector2(ICON_POS.x + ICON * 0.5, 6.5), 6.0)
		draw_colored_polygon(pts, Color(1.0, 0.82, 0.25))
		pts.append(pts[0])
		draw_polyline(pts, Color(0, 0, 0, 0.9), 1.0)

	# XP pips under the unit.
	var xp := mini(int(unit.get("xp", 0)), XP_PIPS)
	var pip := 4.0
	var gap := 2.0
	var row_w := XP_PIPS * pip + (XP_PIPS - 1) * gap
	var px := ICON_POS.x + (ICON - row_w) * 0.5
	var py := ICON_POS.y + ICON + 6.0
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
		var y := ICON_POS.y + ICON - (i + 1) * box_h - i * box_gap
		var r := Rect2(HP_X, y, HP_W, box_h)
		draw_rect(r, Color.WHITE if i < hp else Color(0.9, 0.15, 0.15))
		draw_rect(r, Color(0, 0, 0, 0.85), false, 1.0)
