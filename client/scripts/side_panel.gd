class_name SidePanel
extends VBoxContainer
## The two right-hand panels from the mockup. Upper: details of the
## selected space (owner, value, strategic center, contest, and the units
## there grouped by faction and type). Lower: an event log the network layer
## appends to (bot turns, combat results, alerts).

var _detail: VBoxContainer
var _log: RichTextLabel
var _selected := -1


func _ready() -> void:
	add_theme_constant_override("separation", 8)
	custom_minimum_size = Vector2(330, 0)

	var upper := PanelContainer.new()
	upper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	upper.size_flags_stretch_ratio = 1.3
	upper.add_theme_stylebox_override("panel", HudStyle.box())
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	upper.add_child(scroll)
	_detail = VBoxContainer.new()
	_detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_detail)
	add_child(upper)

	var lower := PanelContainer.new()
	lower.size_flags_vertical = Control.SIZE_EXPAND_FILL
	lower.add_theme_stylebox_override("panel", HudStyle.box())
	var lv := VBoxContainer.new()
	lower.add_child(lv)
	lv.add_child(HudStyle.label("Log", 12, HudStyle.TEXT_DIM))
	_log = RichTextLabel.new()
	_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_log.bbcode_enabled = true
	_log.scroll_following = true
	_log.add_theme_font_size_override("normal_font_size", 12)
	_log.add_theme_color_override("default_color", HudStyle.TEXT)
	lv.add_child(_log)
	add_child(lower)

	GameStore.state_changed.connect(func(): show_space(_selected))
	show_space(-1)


func log_line(text: String) -> void:
	_log.append_text(text + "\n")


func log_events(header: String, events: Array) -> void:
	log_line("[color=#ffd23f]%s[/color]" % header)
	for e in events:
		var line := EventText.describe(e)
		if line != "":
			log_line(line)


func show_space(tid: int) -> void:
	_selected = tid
	for c in _detail.get_children():
		c.queue_free()
	if tid < 0:
		_detail.add_child(HudStyle.label("Nothing selected", 13, HudStyle.TEXT_DIM))
		_detail.add_child(HudStyle.label("Click a territory or sea zone.", 12, HudStyle.TEXT_DIM))
		return
	var t: Dictionary = GameData.territories[tid]
	_detail.add_child(HudStyle.label("%d. %s" % [tid, t["name"]], 16, HudStyle.GOLD))
	var owner := GameStore.owner_of(tid)
	var kind := "Sea zone" if t["type"] == "sea" else "Territory"
	var facts := HBoxContainer.new()
	facts.add_theme_constant_override("separation", 8)
	facts.add_child(HudStyle.label(kind, 12, HudStyle.TEXT_DIM))
	if owner != "" and GameData.factions.has(owner):
		facts.add_child(HudStyle.label("owner: " + owner, 12, GameData.factions[owner].color.lightened(0.45)))
	_detail.add_child(facts)
	if t["type"] == "land":
		var bits := ["value %d" % int(t.get("value", 0))]
		if t.get("strategic_center", false):
			bits.append("Strategic Center")
		_detail.add_child(HudStyle.label(", ".join(bits), 12))
	var contested = _territory_field(tid, "contested_by")
	if contested != null and not contested.is_empty():
		_detail.add_child(HudStyle.label("CONTESTED: " + ", ".join(contested), 12, Color(1.0, 0.5, 0.4)))

	var stacks := GameStore.stacks(tid)
	if stacks.is_empty():
		_detail.add_child(HudStyle.label("No units", 12, HudStyle.TEXT_DIM))
		return
	for code in GameData.faction_order:
		if not stacks.has(code):
			continue
		var head := PanelContainer.new()
		head.add_theme_stylebox_override("panel", HudStyle.box(GameData.factions[code].color.darkened(0.3), GameData.factions[code].color, 1))
		head.add_child(HudStyle.label(GameData.factions[code].name, 12, Color.WHITE))
		_detail.add_child(head)
		for unit_type in stacks[code]:
			_detail.add_child(_unit_row(unit_type, stacks[code][unit_type]))


func _territory_field(tid: int, key: String) -> Variant:
	if GameStore.state.is_empty():
		return null
	var t = GameStore.state["territories"].get(str(tid))
	return null if t == null else t.get(key)


func _unit_row(unit_type: String, count: int) -> Control:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	var icon := TextureRect.new()
	icon.texture = UnitIcons.get_icon(unit_type)
	icon.custom_minimum_size = Vector2(20, 20)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	row.add_child(icon)
	row.add_child(HudStyle.label("%s  x%d" % [unit_type, count], 13))
	return row
