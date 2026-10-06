class_name RulesScreen
extends MenuScreen
## Rules (the main menu's): the player's rule book, "Rules of War" -- res://data/rules_text.json, from
## reference/GC_72 Rules.odt (tools/import_rules.py) -- on a paper sheet over the poster: its sections,
## subheadings, bullet lists, and the Units table with each unit type's icon.

signal back_pressed

const PATH := "res://data/rules_text.json"
const WIDTH := 1040.0
const ICON := 26.0
# The Units table's column widths, by header (others: COLUMN); the last column takes what is left.
const COLUMNS := {"Unit": 190.0, "Type": 52.0, "Special Abilities": 250.0}
const COLUMN := 62.0
const STRIPE := Color(0.043, 0.141, 0.2, 0.07)  # (GCTheme.NAVY, faint: every other row of the table)

var _body: VBoxContainer
var _scroll: ScrollContainer


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(WIDTH, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	var doc := _load()
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 16)
	v.add_child(head)
	var title := HudStyle.heading(str(doc.get("subtitle", "Rules")), 22, true)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(title)
	if str(doc.get("title", "")) != "":
		var game := HudStyle.label(str(doc["title"]), 13, HudStyle.TEXT_DIM)
		game.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		head.add_child(game)
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(_scroll)
	_body = VBoxContainer.new()
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.add_theme_constant_override("separation", 8)
	_scroll.add_child(_body)
	for section in doc.get("sections", []):
		_section(section)
	if (doc.get("sections", []) as Array).is_empty():
		_body.add_child(_text("The rules aren't available (res://data/rules_text.json is missing: run tools/sync_client_data.py).", 15))
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
	if Dbg.args.has("rules_scroll"):  # (scripted runs: a screenshot further down)
		await get_tree().process_frame
		_scroll.scroll_vertical = int(Dbg.args["rules_scroll"])


func close() -> void:
	visible = false


## The rules scroll inside a sheet that leaves the poster showing around it.
func _fit() -> void:
	if _scroll != null and is_inside_tree():
		_scroll.custom_minimum_size = Vector2(0, maxf(240.0, get_viewport_rect().size.y - 260.0))


func _section(section: Dictionary) -> void:
	if str(section.get("heading", "")) != "":
		var gap := Control.new()
		gap.custom_minimum_size = Vector2(0, 6)
		_body.add_child(gap)
		var h := Label.new()
		h.text = str(section["heading"]).to_upper()
		h.add_theme_font_override("font", GCTheme.font("display_black"))
		h.add_theme_font_size_override("font_size", 26)
		h.add_theme_color_override("font_color", GCTheme.RED)
		_body.add_child(h)
	for block in section.get("blocks", []):
		match str(block.get("kind", "")):
			"subheading":
				var space := Control.new()
				space.custom_minimum_size = Vector2(0, 4)
				_body.add_child(space)
				var s := Label.new()
				s.text = str(block["text"]).trim_suffix(":").to_upper()
				s.add_theme_font_override("font", GCTheme.font("display"))
				s.add_theme_font_size_override("font_size", 18)
				s.add_theme_color_override("font_color", GCTheme.NAVY)
				_body.add_child(s)
			"paragraph":
				_body.add_child(_text(str(block["text"]), 15))
			"list":
				var box := VBoxContainer.new()
				box.add_theme_constant_override("separation", 3)
				_list(box, block.get("items", []), 0)
				_body.add_child(box)
			"table":
				_body.add_child(_table(block))


## Bullet lists, nested: each level indented, with its own bullet.
func _list(box: VBoxContainer, items: Array, depth: int) -> void:
	for item in items:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 8)
		var indent := Control.new()
		indent.custom_minimum_size = Vector2(8 + 24 * depth, 0)
		row.add_child(indent)
		var bullet := HudStyle.label(["•", "–", "·"][mini(depth, 2)], 15 if depth == 0 else 14, GCTheme.RED if depth == 0 else HudStyle.TEXT_DIM)
		bullet.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		row.add_child(bullet)
		var text := _text(str(item.get("text", "")), 15 if depth == 0 else 14)
		text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		text.custom_minimum_size = Vector2(WIDTH - 120 - 24 * depth, 0)
		row.add_child(text)
		box.add_child(row)
		_list(box, item.get("items", []), depth + 1)


## The Units table: a header row, then a row per unit type, its first cell the unit's icon and name.
func _table(block: Dictionary) -> Control:
	var header: Array = block.get("header", [])
	var grid := GridContainer.new()
	grid.columns = maxi(1, header.size())
	grid.add_theme_constant_override("h_separation", 0)
	grid.add_theme_constant_override("v_separation", 0)
	for i in header.size():
		var l := _text(str(header[i]), 12, HudStyle.TEXT_DIM)
		l.add_theme_font_override("font", GCTheme.font("display"))
		l.custom_minimum_size = Vector2(_width(str(header[i])), 0)
		l.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
		grid.add_child(_cell(l, -1))
	var r := 0
	for row in block.get("rows", []):
		for i in header.size():
			var value := str(row[i]) if i < row.size() else ""
			var content: Control
			if i == 0:
				content = _unit(value)
			else:
				var l := _text(value, 14)
				l.custom_minimum_size = Vector2(_width(str(header[i])), 0)
				content = l
			grid.add_child(_cell(content, r))
		r += 1
	return grid


func _width(column: String) -> float:
	return COLUMNS.get(column, COLUMN)


## A table cell: padded, every other row striped, the header ruled off underneath.
func _cell(content: Control, row: int) -> Control:
	var p := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = STRIPE if row >= 0 and row % 2 == 0 else Color(0, 0, 0, 0)
	box.content_margin_left = 6
	box.content_margin_right = 6
	box.content_margin_top = 5
	box.content_margin_bottom = 5
	if row < 0:
		box.border_width_bottom = 2
		box.border_color = GCTheme.NAVY
	p.add_theme_stylebox_override("panel", box)
	p.add_child(content)
	return p


## A unit type's icon (its silhouette in navy, as on paper elsewhere) and its name.
func _unit(unit_type: String) -> Control:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 8)
	h.custom_minimum_size = Vector2(_width("Unit"), 0)
	var icon := TextureRect.new()
	icon.texture = UnitIcons.get_icon(unit_type)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	icon.custom_minimum_size = Vector2(ICON, ICON)
	icon.modulate = GCTheme.NAVY
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(icon)
	var label := HudStyle.label(unit_type, 16)
	label.add_theme_font_override("font", GCTheme.font("display"))
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	h.add_child(label)
	return h


func _text(text: String, size: int, color: Color = HudStyle.TEXT) -> Label:
	var l := HudStyle.label(text, size, color)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


static func _load() -> Dictionary:
	if not FileAccess.file_exists(PATH):
		return {}
	var doc = JSON.parse_string(FileAccess.get_file_as_string(PATH))
	return doc if doc is Dictionary else {}
