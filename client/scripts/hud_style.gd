class_name HudStyle
extends RefCounted
## Shared helpers for the HUD, on top of the game's themes (GCTheme: the style guide's navy chrome and
## cream paper). Labels and buttons take a ROLE here and the theme around them decides its colours, so
## the same code reads right on a navy panel or a paper one.
##
## The colour constants below keep their old names. As label colours they stand for roles:
## TEXT the theme's text, TEXT_DIM secondary text (DimLabel), GOLD a heading (HeadingLabel: condensed
## capitals). Used directly (drawing, a box edge) they are the chrome's own colours.

const BG := GCTheme.NAVY
const BG_HEADER := GCTheme.NAVY_LIGHT
const EDGE := GCTheme.NAVY_EDGE
const TEXT := GCTheme.CREAM
const TEXT_DIM := GCTheme.CREAM_DIM
const GOLD := GCTheme.WHITE       # (headings: drawn on the chrome, white)
const ACCENT := GCTheme.RED_LIGHT  # conflict, alerts, emphasis
const RED := GCTheme.RED


## A flat box (kept for the places that colour one themselves): `bg` fill, `edge` border.
static func box(edge: Color = EDGE, bg: Color = BG, edge_w: int = 1) -> StyleBoxFlat:
	return GCTheme.box(bg, edge, edge_w)


## A label of `size` in a role: TEXT (the theme's text), TEXT_DIM (secondary), GOLD (a heading in
## condensed capitals), ACCENT (red, condensed) -- or any other colour, set as it is.
static func label(text: String = "", size: int = 13, color: Color = TEXT) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	if color == TEXT:
		pass
	elif color == TEXT_DIM:
		l.theme_type_variation = "DimLabel"
	elif color == GOLD:
		l.theme_type_variation = "TitleLabel" if size >= 20 else "HeadingLabel"
		l.uppercase = true
		l.add_theme_font_size_override("font_size", size + 2)  # (condensed capitals read smaller)
	elif color == ACCENT:
		l.theme_type_variation = "AccentLabel"
		l.uppercase = true
	else:
		l.add_theme_color_override("font_color", color)
	return l


## A heading: condensed capitals over a 3px rule (red: a conflict section).
static func heading(text: String, size: int = 15, red_rule := false) -> Control:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 0)
	v.add_child(label(text, size, GOLD))
	var rule := HSeparator.new()
	if red_rule:
		rule.theme_type_variation = "RedRule"
	rule.add_theme_constant_override("separation", 4)
	v.add_child(rule)
	return v


## A primary button: red, condensed capitals (Start Game, Next, Roll, OK). The helpers capitalise the
## button's text as it stands; text set later is the caller's to capitalise.
static func primary(b: Button) -> Button:
	b.theme_type_variation = "PrimaryButton"
	b.focus_mode = Control.FOCUS_NONE
	b.text = b.text.to_upper()
	return b


## A secondary button: navy (Resume, Delete, ...). The theme's plain Button already is; this clears any
## earlier role.
static func secondary(b: Button) -> Button:
	b.theme_type_variation = ""
	b.focus_mode = Control.FOCUS_NONE
	b.text = b.text.to_upper()
	return b


## A tab or radio-style choice: red when chosen, navy otherwise.
static func tab(b: Button, chosen: bool) -> Button:
	b.theme_type_variation = "TabButtonOn" if chosen else "TabButton"
	b.focus_mode = Control.FOCUS_NONE
	b.text = b.text.to_upper()
	return b


## A whole paper sheet (the launch screen, a modal panel): cream, a heavy navy frame and offset shadow;
## everything in it uses the paper theme.
static func paper_sheet(p: Control) -> Control:
	p.theme = GCTheme.paper()
	if p is PanelContainer:
		(p as PanelContainer).theme_type_variation = "SheetPanel"
	return p


## A five-point star's outline, centred on `c` (y grows downwards, one point up).
static func star_points(c: Vector2, r_outer: float, r_inner_ratio: float = 0.5) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in 10:
		var ang := -PI / 2.0 + i * PI / 5.0
		pts.append(c + Vector2(cos(ang), sin(ang)) * (r_outer if i % 2 == 0 else r_outer * r_inner_ratio))
	return pts


## Check boxes take their square icons from the theme now (GCTheme); kept so the callers need no change.
static func style_checkbox(_c: CheckBox) -> void:
	pass
