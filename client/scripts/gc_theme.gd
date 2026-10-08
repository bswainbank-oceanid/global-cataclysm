class_name GCTheme
extends RefCounted
## The look of the game, from the style guide (assets/style/GLOBAL_CATACLYSM_STYLE_GUIDE.md and its CSS
## tokens): a 1970s printed strategy game -- navy, signal red and cream, heavy condensed capitals for
## titles and buttons, Inter for text, small radii, hard offset "shadows", no gradients or glow.
##
## Two themes from the same tokens:
##   chrome() -- the dark navy interface around the map (set on the window: everything inherits it)
##   paper()  -- cream "printed" surfaces: the launch screen and the modal panels (set on those panels)
## Labels and buttons take a role rather than a colour (theme type variations), so the same code reads
## right on either:
##   DimLabel, HeadingLabel (condensed capitals), TitleLabel (larger), AccentLabel (red)
##   PrimaryButton (red), Button (secondary: navy), TabButton / TabButtonOn, RadioButton (red when pressed)

# ---- the palette (global-cataclysm.css :root) ---------------------------------------------------
const NAVY := Color("#0B2433")
const NAVY_DARK := Color("#071923")
const NAVY_LIGHT := Color("#183D4F")
const RED := Color("#C92A24")
const RED_DARK := Color("#9E201C")
const RED_LIGHT := Color("#E3483F")
const CREAM := Color("#F4EBDD")
const PAPER := Color("#E9E1D3")
const WHITE := Color("#FFFDF8")
const GRAY := Color("#6B747A")
# derived, for the chrome: muted cream text, and a lighter navy for edges and hovers
const CREAM_DIM := Color("#A9B4B8")
const NAVY_EDGE := Color("#2A5266")
const RADIUS := 4

static var _chrome: Theme
static var _paper: Theme
static var _fonts := {}


## 'display' (Barlow Condensed ExtraBold), 'display_black', 'body' (Inter), 'body_bold', 'body_semibold'.
static func font(name: String) -> Font:
	if _fonts.has(name):
		return _fonts[name]
	var f: Font
	match name:
		"display":
			f = load("res://assets/fonts/BarlowCondensed-ExtraBold.ttf")
		"display_black":
			f = load("res://assets/fonts/BarlowCondensed-Black.ttf")
		"body_bold", "body_semibold":
			var v := FontVariation.new()
			v.base_font = load("res://assets/fonts/Inter.ttf")
			v.variation_opentype = {"wght": 700 if name == "body_bold" else 600}
			f = v
		_:
			f = load("res://assets/fonts/Inter.ttf")
	_fonts[name] = f
	return f


static func chrome() -> Theme:
	if _chrome == null:
		_chrome = _build(false)
	return _chrome


static func paper() -> Theme:
	if _paper == null:
		_paper = _build(true)
	return _paper


## A flat box: `bg` fill, `edge` border `w` px (the bottom `bottom` px: a hard offset shadow), small radius.
const SCROLLBAR := 10  # scroll bars' thickness, px


static func box(bg: Color, edge: Color, w: int = 2, bottom: int = -1, pad := Vector4(8, 5, 8, 5)) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.border_color = edge
	sb.set_border_width_all(w)
	if bottom >= 0:
		sb.border_width_bottom = bottom
	sb.set_corner_radius_all(RADIUS)
	sb.content_margin_left = pad.x
	sb.content_margin_top = pad.y
	sb.content_margin_right = pad.z
	sb.content_margin_bottom = pad.w
	sb.anti_aliasing = false  # crisp, printed edges
	return sb


static func _button_boxes(t: Theme, type: String, fill: Color, edge: Color, hover: Color, text: Color) -> void:
	# a hard 3px bottom "shadow" that the press closes up
	t.set_stylebox("normal", type, box(fill, edge, 2, 4, Vector4(12, 5, 12, 5)))
	t.set_stylebox("hover", type, box(hover, edge, 2, 4, Vector4(12, 5, 12, 5)))
	t.set_stylebox("pressed", type, box(fill.darkened(0.12), edge, 2, 2, Vector4(12, 7, 12, 5)))
	t.set_stylebox("hover_pressed", type, box(hover.darkened(0.08), edge, 2, 2, Vector4(12, 7, 12, 5)))
	t.set_stylebox("focus", type, StyleBoxEmpty.new())
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color", "font_focus_color"]:
		t.set_color(c, type, text)


static func _build(on_paper: bool) -> Theme:
	var t := Theme.new()
	var ink := NAVY if on_paper else CREAM            # body text
	var dim := GRAY if on_paper else CREAM_DIM        # secondary text
	var heading := NAVY_DARK if on_paper else WHITE   # condensed capitals
	var surface := WHITE if on_paper else NAVY        # panels
	var field := WHITE if on_paper else NAVY_DARK     # inputs
	var edge := NAVY if on_paper else NAVY_EDGE       # panel and field borders

	t.default_font = font("body")
	t.default_font_size = 13

	# labels by role
	t.set_color("font_color", "Label", ink)
	for v in ["DimLabel", "HeadingLabel", "TitleLabel", "AccentLabel"]:
		t.set_type_variation(v, "Label")
	t.set_color("font_color", "DimLabel", dim)
	t.set_color("font_color", "HeadingLabel", heading)
	t.set_font("font", "HeadingLabel", font("display"))
	t.set_color("font_color", "TitleLabel", heading)
	t.set_font("font", "TitleLabel", font("display_black"))
	t.set_color("font_color", "AccentLabel", RED if on_paper else RED_LIGHT)
	t.set_font("font", "AccentLabel", font("display"))

	t.set_color("default_color", "RichTextLabel", ink)
	t.set_font("normal_font", "RichTextLabel", font("body"))
	t.set_font("bold_font", "RichTextLabel", font("body_bold"))

	# panels: cream/white with a 2px navy border on paper; navy with a lighter edge in the chrome
	t.set_stylebox("panel", "PanelContainer", box(surface, edge, 2))
	t.set_stylebox("panel", "Panel", box(surface, edge, 2))
	t.set_type_variation("SheetPanel", "PanelContainer")  # a whole paper sheet: a heavier frame and offset shadow
	t.set_stylebox("panel", "SheetPanel", box(PAPER if on_paper else NAVY, NAVY_DARK if on_paper else NAVY_EDGE, 3, 7,
		Vector4(14, 12, 14, 12)))

	# buttons: secondary navy by default; PrimaryButton red; condensed capitals on both
	for type in ["PrimaryButton", "TabButton", "TabButtonOn"]:
		t.set_type_variation(type, "Button")
	t.set_font("font", "Button", font("display"))
	t.set_font_size("font_size", "Button", 15)
	_button_boxes(t, "Button", NAVY if on_paper else NAVY_LIGHT, NAVY_DARK, NAVY_LIGHT if on_paper else Color("#21506A"), CREAM)
	t.set_stylebox("disabled", "Button", box(Color("#C9C2B6") if on_paper else NAVY_DARK, Color("#A39C91") if on_paper else NAVY_LIGHT, 2, 2,
		Vector4(12, 5, 12, 5)))
	t.set_color("font_disabled_color", "Button", Color("#7C786F") if on_paper else Color("#56707C"))
	_button_boxes(t, "PrimaryButton", RED, RED_DARK, RED_LIGHT, WHITE)
	t.set_stylebox("disabled", "PrimaryButton", box(Color("#C9C2B6") if on_paper else NAVY_DARK, Color("#A39C91") if on_paper else NAVY_LIGHT,
		2, 2, Vector4(12, 5, 12, 5)))
	t.set_color("font_disabled_color", "PrimaryButton", Color("#7C786F") if on_paper else Color("#56707C"))
	# tabs and radio-style choices: the chosen one red, the rest navy
	_button_boxes(t, "TabButton", NAVY if on_paper else NAVY_DARK, NAVY_DARK, NAVY_LIGHT, CREAM)
	_button_boxes(t, "TabButtonOn", RED, RED_DARK, RED_LIGHT, WHITE)
	t.set_font_size("font_size", "TabButton", 13)
	t.set_font_size("font_size", "TabButtonOn", 13)
	# a radio choice (a toggle button in a ButtonGroup): a navy tab, red while chosen
	t.set_type_variation("RadioButton", "TabButton")
	for st in ["pressed", "hover_pressed"]:
		t.set_stylebox(st, "RadioButton", box(RED, RED_DARK, 2, 4, Vector4(12, 5, 12, 5)))
	t.set_color("font_pressed_color", "RadioButton", WHITE)
	t.set_color("font_hover_pressed_color", "RadioButton", WHITE)

	# inputs
	for type in ["LineEdit", "TextEdit"]:
		t.set_stylebox("normal", type, box(field, edge, 2))
		t.set_stylebox("focus", type, box(field, RED, 2))
		t.set_stylebox("read_only", type, box(field.darkened(0.05), edge, 1))
		t.set_color("font_color", type, ink)
		t.set_color("font_placeholder_color", type, dim)
		t.set_color("caret_color", type, ink)
		t.set_color("selection_color", type, Color(RED, 0.35))
	t.set_stylebox("normal", "OptionButton", box(field, edge, 2, 2, Vector4(8, 4, 8, 4)))
	t.set_stylebox("hover", "OptionButton", box(field, RED if on_paper else CREAM_DIM, 2, 2, Vector4(8, 4, 8, 4)))
	t.set_stylebox("pressed", "OptionButton", box(field, RED, 2, 2, Vector4(8, 4, 8, 4)))
	t.set_stylebox("disabled", "OptionButton", box(field.darkened(0.04), Color("#B8B0A3") if on_paper else NAVY_LIGHT, 1, 1,
		Vector4(8, 4, 8, 4)))
	t.set_stylebox("focus", "OptionButton", StyleBoxEmpty.new())
	t.set_font("font", "OptionButton", font("body_semibold"))
	t.set_font_size("font_size", "OptionButton", 13)
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color", "font_hover_pressed_color"]:
		t.set_color(c, "OptionButton", ink)
	t.set_color("font_disabled_color", "OptionButton", dim)
	# the drop-down lists: always navy
	t.set_stylebox("panel", "PopupMenu", box(NAVY, NAVY_EDGE, 2, -1, Vector4(4, 4, 4, 4)))
	t.set_stylebox("hover", "PopupMenu", box(RED, RED, 0, -1, Vector4(4, 2, 4, 2)))
	t.set_color("font_color", "PopupMenu", CREAM)
	t.set_color("font_hover_color", "PopupMenu", WHITE)
	t.set_color("font_disabled_color", "PopupMenu", Color("#56707C"))
	t.set_color("font_separator_color", "PopupMenu", CREAM_DIM)
	t.set_font("font", "PopupMenu", font("body"))

	# check boxes: our own square icons, red-ticked
	t.set_icon("checked", "CheckBox", _check_icon(true, false, on_paper))
	t.set_icon("unchecked", "CheckBox", _check_icon(false, false, on_paper))
	t.set_icon("checked_disabled", "CheckBox", _check_icon(true, true, on_paper))
	t.set_icon("unchecked_disabled", "CheckBox", _check_icon(false, true, on_paper))
	for s in ["normal", "hover", "pressed", "hover_pressed", "disabled", "focus"]:
		t.set_stylebox(s, "CheckBox", StyleBoxEmpty.new())
	t.set_font("font", "CheckBox", font("body"))
	t.set_font_size("font_size", "CheckBox", 13)
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color", "font_focus_color"]:
		t.set_color(c, "CheckBox", ink)
	t.set_color("font_disabled_color", "CheckBox", dim)

	# rules: 3px navy (red: RedRule)
	var rule := StyleBoxLine.new()
	rule.color = NAVY if on_paper else NAVY_EDGE
	rule.thickness = 3
	t.set_stylebox("separator", "HSeparator", rule)
	t.set_constant("separation", "HSeparator", 8)
	t.set_type_variation("RedRule", "HSeparator")
	var red_rule := StyleBoxLine.new()
	red_rule.color = RED
	red_rule.thickness = 3
	t.set_stylebox("separator", "RedRule", red_rule)

	# tab containers (the log tabs): the chosen tab red
	t.set_stylebox("tab_selected", "TabContainer", box(RED, RED_DARK, 0, -1, Vector4(10, 3, 10, 3)))
	t.set_stylebox("tab_unselected", "TabContainer", box(NAVY_DARK, NAVY_DARK, 0, -1, Vector4(10, 3, 10, 3)))
	t.set_stylebox("tab_hovered", "TabContainer", box(NAVY_LIGHT, NAVY_LIGHT, 0, -1, Vector4(10, 3, 10, 3)))
	t.set_stylebox("panel", "TabContainer", StyleBoxEmpty.new())
	t.set_font("font", "TabContainer", font("display"))
	t.set_font_size("font_size", "TabContainer", 13)
	t.set_color("font_selected_color", "TabContainer", WHITE)
	t.set_color("font_unselected_color", "TabContainer", CREAM_DIM)
	t.set_color("font_hovered_color", "TabContainer", CREAM)

	# spin boxes' fields come from LineEdit; tooltips: navy, cream text
	t.set_stylebox("panel", "TooltipPanel", box(NAVY_DARK, NAVY_EDGE, 2, -1, Vector4(8, 5, 8, 5)))
	t.set_color("font_color", "TooltipLabel", CREAM)
	t.set_font("font", "TooltipLabel", font("body"))
	t.set_font_size("font_size", "TooltipLabel", 12)

	# scroll bars: thin, flat -- SCROLLBAR px thick (a scroll bar is as thick as its styles' margins: with
	# none it has no thickness at all, and doesn't show)
	var m := SCROLLBAR / 2.0
	var pad := Vector4(m, m, m, m)
	for bar in ["VScrollBar", "HScrollBar"]:
		t.set_stylebox("scroll", bar, box(Color(0, 0, 0, 0.12) if on_paper else NAVY_DARK, Color(0, 0, 0, 0), 0, -1, pad))
		t.set_stylebox("grabber", bar, box(NAVY_LIGHT if on_paper else NAVY_EDGE, Color(0, 0, 0, 0), 0, -1, pad))
		t.set_stylebox("grabber_highlight", bar, box(RED, Color(0, 0, 0, 0), 0, -1, pad))
		t.set_stylebox("grabber_pressed", bar, box(RED_DARK, Color(0, 0, 0, 0), 0, -1, pad))
	return t


static func _check_icon(checked: bool, disabled: bool, on_paper: bool) -> ImageTexture:
	var n := 18
	var img := Image.create(n, n, false, Image.FORMAT_RGBA8)
	var edge := (NAVY if on_paper else CREAM) if not disabled else (Color("#A39C91") if on_paper else Color("#56707C"))
	var fill := WHITE if on_paper else NAVY_DARK
	for y in n:
		for x in n:
			img.set_pixel(x, y, edge if (x < 2 or y < 2 or x >= n - 2 or y >= n - 2) else fill)
	if checked:
		var tick := RED if not disabled else Color("#A39C91")
		for y in range(4, n - 4):
			for x in range(4, n - 4):
				img.set_pixel(x, y, tick)  # a solid square: a printed check mark
	return ImageTexture.create_from_image(img)
