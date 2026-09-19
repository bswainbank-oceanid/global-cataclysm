class_name MapContested
extends Node2D
## Diagonal black stripes over spaces that are contested (a battle is still
## open there) or are about to be (a queued combat move ends there). Stripes
## are a shader pattern in MAP space, so they stay put while panning, and
## their period follows the zoom so they read as a steady ~10px hatch.
##
## Land stripes sit above the ownership fill. A sea zone's polygon is its
## outer boundary and encloses coastal land, so sea stripes (Kind.SEA) go in
## a layer beneath the ownership fill, like the sea highlight.

enum Kind { LAND, SEA }

const SHADER := """
shader_type canvas_item;
uniform float period = 20.0;
uniform vec4 stripe_color : source_color = vec4(0.0, 0.0, 0.0, 0.6);
varying vec2 world;
void vertex() { world = VERTEX; }
void fragment() {
	float d = (world.x + world.y) / period;
	float f = fract(d);
	float aa = max(fwidth(d), 0.001);
	float band = smoothstep(0.0, aa, f) * (1.0 - smoothstep(0.4, 0.4 + aa, f));
	COLOR = vec4(stripe_color.rgb, stripe_color.a * band);
}
"""

var kind := Kind.LAND
var _material := ShaderMaterial.new()


func _ready() -> void:
	var shader := Shader.new()
	shader.code = SHADER
	_material.shader = shader
	material = _material
	GameStore.state_changed.connect(queue_redraw)


func set_zoom(z: float) -> void:
	_material.set_shader_parameter("period", 12.0 / z)


func _draw() -> void:
	var want_sea := kind == Kind.SEA
	for tid in GameStore.contested_spaces():
		if (GameData.territories[tid]["type"] == "sea") != want_sea:
			continue
		for copy in [-1, 0, 1]:
			var shift := Vector2(copy * GameData.map_w, 0)
			for poly in GameData.fill_shapes[tid]:
				var moved := PackedVector2Array()
				for p in poly:
					moved.append(p + shift)
				draw_colored_polygon(moved, Color.WHITE)
