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
