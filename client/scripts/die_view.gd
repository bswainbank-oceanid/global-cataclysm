class_name DieView
extends Control
## A die seen from above, with the rolled number on its top face. The outline
## is the die's shape as seen from the top: D6 square, D8 triangle, D10 kite,
## D12 pentagon. The rim shows the result: green for a hit, yellow for a hit
## that only landed through the max-die bypass, dim for a miss.

const SIZE := 38.0

var die := "D6"
var roll := 1
var hit := false
var bypass := false
var tint := Color(0.9, 0.9, 0.95)


static func make(die_name: String, value: int, is_hit: bool, is_bypass: bool, colour: Color) -> DieView:
	var d := DieView.new()
	d.die = die_name
	d.roll = value
	d.hit = is_hit
	d.bypass = is_bypass
	d.tint = colour
	d.custom_minimum_size = Vector2(SIZE, SIZE)
	d.size = Vector2(SIZE, SIZE)
	d.mouse_filter = Control.MOUSE_FILTER_PASS
	return d


func _outline() -> PackedVector2Array:
	var c := Vector2(SIZE, SIZE) * 0.5
	var r := SIZE * 0.5 - 2.0
	match die:
		"D6":
			return PackedVector2Array([c + Vector2(-r, -r) * 0.86, c + Vector2(r, -r) * 0.86, c + Vector2(r, r) * 0.86, c + Vector2(-r, r) * 0.86])
		"D8":
			return PackedVector2Array([c + Vector2(0, -r), c + Vector2(r * 0.95, r * 0.8), c + Vector2(-r * 0.95, r * 0.8)])
		"D10":
			return PackedVector2Array([c + Vector2(0, -r), c + Vector2(r * 0.85, -r * 0.1), c + Vector2(0, r), c + Vector2(-r * 0.85, -r * 0.1)])
		_:  # D12
			var pts := PackedVector2Array()
			for i in 5:
				var a := -PI / 2.0 + i * TAU / 5.0
				pts.append(c + Vector2(cos(a), sin(a)) * r)
			return pts


func _draw() -> void:
	var pts := _outline()
	var rim := Color(0.35, 0.9, 0.4) if hit and not bypass else (Color(1.0, 0.85, 0.25) if hit else Color(0.5, 0.55, 0.62))
	draw_colored_polygon(pts, tint.darkened(0.15))
	var loop := PackedVector2Array(pts)
	loop.append(pts[0])
	draw_polyline(loop, Color(0, 0, 0, 0.9), 4.0, true)
	draw_polyline(loop, rim, 2.5, true)
	var font := ThemeDB.fallback_font
	var text := str(roll)
	var fs := 15
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_CENTER, -1, fs).x
	var at := Vector2((SIZE - w) * 0.5, SIZE * 0.5 + 5.5)
	draw_string_outline(font, at, text, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, 3, Color(0, 0, 0, 0.9))
	draw_string(font, at, text, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, Color.WHITE)
	# The die's name, small, under the number.
	draw_string(font, Vector2((SIZE - 14.0) * 0.5 - 1.0, SIZE - 2.0), die, HORIZONTAL_ALIGNMENT_LEFT, -1, 8, Color(1, 1, 1, 0.75))
