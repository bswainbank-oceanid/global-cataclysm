class_name UnitIcons
extends RefCounted
## Tintable unit glyphs. The supplied SVGs are white shapes on a black square
## (game-icons.net style); the black square is stripped here so the glyph can
## sit on a faction-coloured badge. Rasterised once at 64px with mipmaps so
## they stay clean when drawn at ~14px.

const FILES := {
	"Infantry": "person",
	"Mechanized Infantry": "apc",
	"Armor": "battle-tank",
	"Fighter": "jet-fighter",
	"Bomber": "bomber",
	"Aircraft Carrier": "carrier",
	"Cruiser": "cruiser",
	"Submarine": "submarine",
	"Transport": "cargo-ship",
}
const BLACK_SQUARE := '<path d="M0 0h512v512H0z"/>'
const RASTER_SCALE := 0.125  # 512px viewBox -> 64px

static var _cache := {}


static func get_icon(unit_type: String) -> Texture2D:
	if _cache.has(unit_type):
		return _cache[unit_type]
	var tex: Texture2D = null
	if FILES.has(unit_type):
		var text := FileAccess.get_file_as_string("res://assets/icons/%s.svg" % FILES[unit_type])
		var img := Image.new()
		if img.load_svg_from_string(text.replace(BLACK_SQUARE, ""), RASTER_SCALE) == OK:
			img.generate_mipmaps()
			tex = ImageTexture.create_from_image(img)
	_cache[unit_type] = tex
	return tex
