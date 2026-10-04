class_name UnitIcons
extends RefCounted
## Tintable unit glyphs. Each unit type's SVG is drawn as a white glyph so it can sit on a faction-
## coloured badge: an emblem traced in its own colour (assets/icons/units) is recoloured white, and
## an older white-on-black-square icon (game-icons.net style) has its black square stripped. Rasterised once at 64px with mipmaps so
## they stay clean when drawn at ~14px. Each unit type's icon file comes from
## its unit set (GameData.unit_def(type)["icon"], synced into res://assets/icons).

const BLACK_SQUARE := '<path d="M0 0h512v512H0z"/>'
const RASTER_SCALE := 0.125  # 512px viewBox -> 64px

static var _cache := {}
static var _FILL := _fill_regex()


static func _fill_regex() -> RegEx:
	var re := RegEx.new()
	re.compile('fill="#[0-9A-Fa-f]{3,8}"')
	return re


static func get_icon(unit_type: String) -> Texture2D:
	if _cache.has(unit_type):
		return _cache[unit_type]
	var tex: Texture2D = null
	# The GameData autoload, looked up at run time: a headless test script compiles this
	# class before the autoloads exist, so it can't be named directly here.
	var game_data := (Engine.get_main_loop() as SceneTree).root.get_node_or_null("GameData")
	var file = game_data.unit_def(unit_type).get("icon") if game_data != null else null
	if file != null and str(file) != "":
		var text := FileAccess.get_file_as_string("res://assets/icons/%s" % file).replace(BLACK_SQUARE, "")
		text = _FILL.sub(text, 'fill="#fff"', true)  # (an emblem in its own colour -> white, to be tinted)
		var img := Image.new()
		if img.load_svg_from_string(text, RASTER_SCALE) == OK:
			img.generate_mipmaps()
			tex = ImageTexture.create_from_image(img)
	_cache[unit_type] = tex
	return tex
