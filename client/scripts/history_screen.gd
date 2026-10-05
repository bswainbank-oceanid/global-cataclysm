class_name HistoryScreen
extends MenuScreen
## History (the main menu's): the game's back story, "History of the World in the 20th Century" --
## res://data/history.json, from reference/GC_ 1972 History.odt (tools/import_history.py) -- year by
## year on a paper sheet over the poster.

signal back_pressed

const PATH := "res://data/history.json"

var _story: VBoxContainer
var _scroll: ScrollContainer


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(860, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	var doc := _load()
	v.add_child(HudStyle.heading(str(doc.get("title", "History")), 22, true))
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(_scroll)
	_story = VBoxContainer.new()
	_story.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_story.add_theme_constant_override("separation", 14)
	_scroll.add_child(_story)
	for p in doc.get("introduction", []):
		_story.add_child(_paragraph(str(p)))
	var first := true
	for section in doc.get("sections", []):
		if not first:
			_story.add_child(HSeparator.new())
		first = false
		_story.add_child(_section(section))
	if (doc.get("sections", []) as Array).is_empty():
		_story.add_child(_paragraph("The history isn't available (res://data/history.json is missing: run tools/sync_client_data.py)."))
	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 40)
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back.pressed.connect(func(): back_pressed.emit())
	v.add_child(back)
	get_viewport().size_changed.connect(_fit)
	_fit()


func open() -> void:
	_scroll.scroll_vertical = 0
	_fit()
	visible = true


func close() -> void:
	visible = false


## The story scrolls inside a sheet that leaves the poster showing around it.
func _fit() -> void:
	if _scroll != null and is_inside_tree():
		_scroll.custom_minimum_size = Vector2(0, maxf(240.0, get_viewport_rect().size.y - 260.0))


func _section(section: Dictionary) -> Control:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 14)
	var year := Label.new()
	year.text = str(section.get("year", ""))
	year.add_theme_font_override("font", GCTheme.font("display_black"))
	year.add_theme_font_size_override("font_size", 34)
	year.add_theme_color_override("font_color", GCTheme.RED)
	head.add_child(year)
	var headline := Label.new()
	headline.text = str(section.get("headline", "")).to_upper()
	headline.add_theme_font_override("font", GCTheme.font("display"))
	headline.add_theme_font_size_override("font_size", 20)
	headline.add_theme_color_override("font_color", GCTheme.NAVY)
	headline.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	headline.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	headline.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	head.add_child(headline)
	box.add_child(head)
	for p in section.get("paragraphs", []):
		box.add_child(_paragraph(str(p)))
	return box


func _paragraph(text: String) -> Label:
	var l := HudStyle.label(text, 15)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.custom_minimum_size = Vector2(800, 0)
	return l


static func _load() -> Dictionary:
	if not FileAccess.file_exists(PATH):
		return {}
	var doc = JSON.parse_string(FileAccess.get_file_as_string(PATH))
	return doc if doc is Dictionary else {}
