class_name MenuScreen
extends Control
## A full-window screen outside the game (login, main menu, lobbies): the splash poster behind, and
## helpers for the cream paper sheets laid over it. Subclasses build their content in _build().

const ART := "res://assets/logo/splash.png"


func _ready() -> void:
	z_index = 600  # above the game (and the one-game launcher)
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var bg := ColorRect.new()
	bg.color = GCTheme.NAVY_DARK
	add_child(bg)
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var art := TextureRect.new()
	art.texture = load(ART)
	art.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	art.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	art.modulate = Color(1, 1, 1).darkened(_art_dim())
	art.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(art)
	art.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_build()


## How much the poster is darkened behind this screen's sheets (0 = not at all).
func _art_dim() -> float:
	return 0.45


func _build() -> void:
	pass


## A CenterContainer filling `parent` (a screen) -- inside a ScrollContainer, so a sheet too big for the window
## gets scroll bars instead of running off it (one that fits stays centred).
static func scroll_center(parent: Control) -> CenterContainer:
	var scroll := ScrollContainer.new()
	parent.add_child(scroll)
	scroll.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var center := CenterContainer.new()
	center.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	center.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.add_child(center)
	return center


## A paper sheet of `width`, holding a VBoxContainer (returned) -- add it where it belongs.
static func sheet(width: float, separation := 10) -> PanelContainer:
	var panel := PanelContainer.new()
	HudStyle.paper_sheet(panel)
	panel.custom_minimum_size = Vector2(width, 0)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", separation)
	panel.add_child(v)
	return panel


static func body(panel: PanelContainer) -> VBoxContainer:
	return panel.get_child(0) as VBoxContainer


## The wordmark: GLOBAL CATACLYSM 1972, CATACLYSM in red, over a red rule.
static func wordmark(size := 34) -> Control:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 4)
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	row.add_theme_constant_override("separation", 12)
	for word in ["Global", "Cataclysm", "1972"]:
		var w := HudStyle.label(word, size, HudStyle.GOLD)
		if word == "Cataclysm":
			w.add_theme_color_override("font_color", GCTheme.RED)
		row.add_child(w)
	v.add_child(row)
	var rule := HSeparator.new()
	rule.theme_type_variation = "RedRule"
	v.add_child(rule)
	return v


## A labelled text field: [label above, LineEdit]; `secret` hides what is typed.
static func field(parent: Control, caption: String, secret := false, placeholder := "") -> LineEdit:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 3)
	box.add_child(HudStyle.label(caption, 13, HudStyle.TEXT_DIM))
	var edit := LineEdit.new()
	edit.secret = secret
	edit.placeholder_text = placeholder
	edit.custom_minimum_size = Vector2(0, 34)
	box.add_child(edit)
	parent.add_child(box)
	return edit


## A red line for a problem the server reported (empty: hidden).
static func problem_label() -> Label:
	var l := HudStyle.label("", 13, GCTheme.RED)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.visible = false
	return l
