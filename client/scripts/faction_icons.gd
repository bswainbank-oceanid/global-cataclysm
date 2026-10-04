class_name FactionIcons
extends RefCounted
## Faction glyphs (the same shapes exports/map.png draws), one per faction -- its faction set's `icon`,
## synced into res://assets/icons. Drawn flat, as the style guide asks: the files' dark outline is left
## off, so each is a single white shape, rasterised once at 64px with mipmaps and tinted where shown.

const RASTER_SCALE := 0.125  # 512px viewBox -> 64px

static var _cache := {}


static func get_icon(code: String) -> Texture2D:
	if _cache.has(code):
		return _cache[code]
	var tex: Texture2D = null
	# The GameData autoload, looked up at run time (a headless test compiles this before autoloads exist).
	var game_data := (Engine.get_main_loop() as SceneTree).root.get_node_or_null("GameData")
	var file := ""
	if game_data != null and game_data.factions.has(code):
		file = str(game_data.factions[code].get("icon", ""))
	if file != "":
		var img := Image.new()
		var svg := FileAccess.get_file_as_string("res://assets/icons/%s" % file)
		var no_stroke := RegEx.create_from_string("stroke=\"[^\"]*\"")
		svg = no_stroke.sub(svg, "stroke=\"none\"", true)
		if img.load_svg_from_string(svg, RASTER_SCALE) == OK:
			img.generate_mipmaps()
			tex = ImageTexture.create_from_image(img)
	_cache[code] = tex
	return tex


## A `px`-square icon of faction `code` in its colour (lightened on the navy chrome, to read there; as it
## is on paper), with a tooltip naming the faction; an empty spacer if it has none.
static func make(code: String, px: float = 18.0, on_paper := false) -> Control:
	var tex := get_icon(code)
	var game_data := (Engine.get_main_loop() as SceneTree).root.get_node_or_null("GameData")
	if tex == null or game_data == null:
		var c := Control.new()
		c.custom_minimum_size = Vector2(0, px)
		return c
	var r := TextureRect.new()
	r.texture = tex
	r.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	r.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	r.custom_minimum_size = Vector2(px, px)
	r.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var col := game_data.factions[code].color as Color
	r.modulate = col if on_paper else col.lightened(0.45)
	r.tooltip_text = str(game_data.factions[code].name)
	r.mouse_filter = Control.MOUSE_FILTER_PASS
	return r
