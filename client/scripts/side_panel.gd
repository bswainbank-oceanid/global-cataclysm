class_name SidePanel
extends VBoxContainer
## The two right-hand panels from the mockup. Upper: details of the
## selected space (owner, value, strategic center, contest, and the units
## there grouped by faction and type) -- later also where orders get picked.
## Lower: the orders QUEUED for the current phase (what Next will execute),
## above a log of the phases already executed.

var _detail: VBoxContainer
var _queue: RichTextLabel
var _queue_head: Label
var _log: RichTextLabel
var _selected := -1
var _next: Button


func _ready() -> void:
	add_theme_constant_override("separation", 8)
	custom_minimum_size = Vector2(330, 0)

	var upper := PanelContainer.new()
	upper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	upper.size_flags_stretch_ratio = 0.5  # the log is the busier pane while stepping
	upper.add_theme_stylebox_override("panel", HudStyle.box())
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	upper.add_child(scroll)
	_detail = VBoxContainer.new()
	_detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_detail)
	add_child(upper)

	_next = Button.new()
	_next.custom_minimum_size = Vector2(0, 46)
	_next.focus_mode = Control.FOCUS_NONE
	_next.add_theme_font_size_override("font_size", 15)
	_next.add_theme_color_override("font_color", HudStyle.GOLD)
	_next.add_theme_color_override("font_hover_color", Color.WHITE)
	_next.add_theme_color_override("font_disabled_color", HudStyle.TEXT_DIM)
	_next.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	_next.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.24, 0.2, 0.06), 2))
	_next.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, Color(0.3, 0.25, 0.08), 2))
	_next.add_theme_stylebox_override("disabled", HudStyle.box(HudStyle.EDGE, HudStyle.BG, 1))
	var key := InputEventKey.new()
	key.keycode = KEY_SPACE
	var sc := Shortcut.new()
	sc.events = [key]
	_next.shortcut = sc
	_next.shortcut_in_tooltip = false
	_next.tooltip_text = "Step to the next phase (Space)"
	_next.pressed.connect(Stepper.advance)
	add_child(_next)
	Stepper.changed.connect(_sync_next)
	_sync_next()

	var lower := PanelContainer.new()
	lower.size_flags_vertical = Control.SIZE_EXPAND_FILL
	lower.size_flags_stretch_ratio = 1.5
	lower.add_theme_stylebox_override("panel", HudStyle.box())
	var lv := VBoxContainer.new()
	lower.add_child(lv)
	_queue_head = HudStyle.label("Queued orders", 12, HudStyle.GOLD)
	lv.add_child(_queue_head)
	_queue = _rich_text()
	_queue.scroll_following = false
	lv.add_child(_queue)
	lv.add_child(HSeparator.new())
	lv.add_child(HudStyle.label("Executed", 12, HudStyle.TEXT_DIM))
	_log = _rich_text()
	_log.scroll_following = true
	lv.add_child(_log)
	add_child(lower)

	GameStore.state_changed.connect(func(): show_space(_selected))
	show_space(-1)


func _sync_next() -> void:
	_next.text = Stepper.button_text
	_next.disabled = not Stepper.button_enabled


func _rich_text() -> RichTextLabel:
	var r := RichTextLabel.new()
	r.size_flags_vertical = Control.SIZE_EXPAND_FILL
	r.bbcode_enabled = true
	r.add_theme_font_size_override("normal_font_size", 12)
	r.add_theme_font_size_override("bold_font_size", 12)  # default bold is larger, which made battle headings tower over the text
	r.add_theme_color_override("default_color", HudStyle.TEXT)
	return r


## The orders waiting for Next: what the current phase's bot decided (or, for
## an automatic phase, what running it will do).
func show_queue(header: String, skipped: Array, events: Array) -> void:
	_queue_head.text = "Queued  -  %s" % header
	_queue.clear()
	for phase in skipped:
		_queue.append_text("[color=#7f8ea0]%s skipped (not allowed on a faction's first turn)[/color]
" % GameStore.PHASE_LABELS.get(phase, phase))
	var shown := 0
	for e in events:
		var line := EventText.describe(e)
		if line != "":
			_queue.append_text(line + "
")
			shown += 1
	if shown == 0:
		_queue.append_text("[color=#7f8ea0]  (nothing queued)[/color]
")


func log_line(text: String) -> void:
	_log.append_text(text + "\n")


func log_events(header: String, events: Array) -> void:
	log_line("[color=#ffd23f]%s[/color]" % header)
	var shown := 0
	for e in events:
		var line := EventText.describe(e)
		if line != "":
			log_line(line)
			shown += 1
	if shown == 0:
		log_line("[color=#7f8ea0]  (nothing happened)[/color]")


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
