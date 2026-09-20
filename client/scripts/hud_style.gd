class_name HudStyle
extends RefCounted
## Shared look for HUD panels: dark slate boxes with a coloured edge.

const BG := Color(0.085, 0.105, 0.135)
const BG_HEADER := Color(0.13, 0.155, 0.19)
const EDGE := Color(0.26, 0.31, 0.38)
const TEXT := Color(0.9, 0.93, 0.96)
const TEXT_DIM := Color(0.6, 0.66, 0.74)
const GOLD := Color(1.0, 0.82, 0.25)


static func box(edge: Color = EDGE, bg: Color = BG, edge_w: int = 1) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.border_color = edge
	sb.set_border_width_all(edge_w)
	sb.set_corner_radius_all(3)
	sb.content_margin_left = 8
	sb.content_margin_right = 8
	sb.content_margin_top = 5
	sb.content_margin_bottom = 5
	return sb


static func label(text: String = "", size: int = 13, color: Color = TEXT) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	return l


## A five-point star's outline, centred on `c` (y grows downwards, one point up).
static func star_points(c: Vector2, r_outer: float, r_inner_ratio: float = 0.5) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in 10:
		var ang := -PI / 2.0 + i * PI / 5.0
		pts.append(c + Vector2(cos(ang), sin(ang)) * (r_outer if i % 2 == 0 else r_outer * r_inner_ratio))
	return pts


static var _box_icons := {}


## A visible checkbox: Godot's default icons vanish (unchecked) or blur (checked) on
## these dark panels, so draw our own -- an outlined square, gold-ticked when checked.
static func style_checkbox(c: CheckBox) -> void:
	if _box_icons.is_empty():
		for state in ["checked", "unchecked"]:
			for disabled in [false, true]:
				_box_icons["%s_%s" % [state, disabled]] = _make_box(state == "checked", disabled)
	c.add_theme_icon_override("checked", _box_icons["checked_false"])
	c.add_theme_icon_override("unchecked", _box_icons["unchecked_false"])
	c.add_theme_icon_override("checked_disabled", _box_icons["checked_true"])
	c.add_theme_icon_override("unchecked_disabled", _box_icons["unchecked_true"])
	c.add_theme_color_override("font_disabled_color", TEXT_DIM)


static func _make_box(checked: bool, disabled: bool) -> ImageTexture:
	var n := 18
	var img := Image.create(n, n, false, Image.FORMAT_RGBA8)
	var edge := Color(0.42, 0.47, 0.55) if disabled else Color(0.8, 0.85, 0.92)
	var fill := Color(0.09, 0.105, 0.135)
	for y in n:
		for x in n:
			var on_edge: bool = x < 2 or y < 2 or x >= n - 2 or y >= n - 2
			img.set_pixel(x, y, edge if on_edge else fill)
	if checked:
		var tick := Color(0.55, 0.5, 0.3) if disabled else GOLD
		for i in 4:  # the short stroke down-right, then the long stroke up-right
			for t in 2:
				img.set_pixel(4 + i, 9 + i + t, tick)
		for i in 7:
			for t in 2:
				img.set_pixel(7 + i, 12 - i + t, tick)
	return ImageTexture.create_from_image(img)
