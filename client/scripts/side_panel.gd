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
signal territory_clicked(tid: int)  # a territory name in the queue/log was clicked
signal units_selected(unit_ids: Array)  # the units toggled on in the selection panel

signal purchase_add(unit_type: String, tid: int)
signal purchase_remove(unit_type: String, tid: int)

var _selected := -1
var _selected_units := {}  # unit_id -> true; survives the panel rebuilding on every state change
var _orders: OrdersPanel
var _next: HoldButton


func _ready() -> void:
	add_theme_constant_override("separation", 8)
	custom_minimum_size = Vector2(330, 0)

	var upper := PanelContainer.new()
	upper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	upper.size_flags_stretch_ratio = 0.8  # room for a stack of unit tiles; the queue/log pane is still the larger one
	upper.add_theme_stylebox_override("panel", HudStyle.box())
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	upper.add_child(scroll)
	_detail = VBoxContainer.new()
	_detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_detail)
	add_child(upper)

	_orders = OrdersPanel.new()
	add_child(_orders)
	_orders.add_requested.connect(func(u: String, tid: int): purchase_add.emit(u, tid))
	_orders.remove_requested.connect(func(u: String, tid: int): purchase_remove.emit(u, tid))
	_next = _orders.button
	_next.tooltip_text = "Step to the next phase (Space)"
	_next.activated.connect(Stepper.button_pressed)
	Stepper.changed.connect(_sync_next)
	GameStore.purchase_changed.connect(func(): show_queue_again())
	_sync_next()

	var lower := PanelContainer.new()
	lower.size_flags_vertical = Control.SIZE_EXPAND_FILL
	lower.size_flags_stretch_ratio = 1.0
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


## Dev/scripted: hold the submit button for `seconds` (and leave it held).
func debug_hold(seconds: float) -> void:
	_next._start_hold()
	await get_tree().create_timer(seconds).timeout


func _sync_next() -> void:
	_next.text = Stepper.button_text
	_next.disabled = not Stepper.button_active
	_next.hold_seconds = 1.0 if Stepper.needs_hold else 0.0
	_next.tooltip_text = "Hold for 1 second to submit (mouse or Space)" if Stepper.needs_hold else "Step to the next phase (Space)"


func _rich_text() -> RichTextLabel:
	var r := RichTextLabel.new()
	r.size_flags_vertical = Control.SIZE_EXPAND_FILL
	r.bbcode_enabled = true
	r.add_theme_font_size_override("normal_font_size", 12)
	r.add_theme_font_size_override("bold_font_size", 12)  # default bold is larger, which made battle headings tower over the text
	r.add_theme_color_override("default_color", HudStyle.TEXT)
	r.meta_clicked.connect(_on_meta)
	r.meta_hover_started.connect(func(_m): r.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND)
	r.meta_hover_ended.connect(func(_m): r.mouse_default_cursor_shape = Control.CURSOR_ARROW)
	return r


## A link in the queue/log: a territory name (its id) centres the map on it; a
## "dec:<unit>:<territory>" link removes one queued purchase of that unit there.
func _on_meta(meta: Variant) -> void:
	var s := str(meta)
	if s.begins_with("dec:"):
		var parts := s.split(":")
		purchase_remove.emit(parts[1], int(parts[2]))
		return
	territory_clicked.emit(int(s))


var _last_queue := {}  # header, skipped, events -- to redraw the queue when only the purchase options changed


## The orders waiting for Next: what the current phase's bot decided (or, for
## an automatic phase, what running it will do). For the player's own Purchase
## phase they are the player's queue, each order with a "-" link.
func show_queue(header: String, skipped: Array, events: Array) -> void:
	_last_queue = {"header": header, "skipped": skipped, "events": events}
	_queue_head.text = "Queued  -  %s" % header
	_queue.clear()
	for phase in skipped:
		_queue.append_text("[color=#7f8ea0]%s skipped (not allowed on a faction's first turn)[/color]\n" % GameStore.PHASE_LABELS.get(phase, phase))
	if GameStore.human_purchase_active():
		var hp: Dictionary = GameStore.human_purchase
		_queue.append_text(EventText.describe_editable_purchase(hp["orders"], int(hp["total_cost"]), int(hp["treasury"])) + "\n")
		return
	var shown := 0
	for e in events:
		var line := EventText.describe(e)
		if line != "":
			_queue.append_text(line + "\n")
			shown += 1
	if shown == 0:
		_queue.append_text("[color=#7f8ea0]  (nothing queued)[/color]\n")


func show_queue_again() -> void:
	if not _last_queue.is_empty():
		show_queue(_last_queue["header"], _last_queue["skipped"], _last_queue["events"])


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
	if tid != _selected and not _selected_units.is_empty():
		_selected_units.clear()
		units_selected.emit([])
	_selected = tid
	_orders.set_target(tid)
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

	var by_owner := {}
	for u in GameStore.units_at(tid):
		if not by_owner.has(u["owner"]):
			by_owner[u["owner"]] = []
		by_owner[u["owner"]].append(u)
	if by_owner.is_empty():
		_detail.add_child(HudStyle.label("No units", 12, HudStyle.TEXT_DIM))
		return
	for code in GameData.faction_order:
		if not by_owner.has(code):
			continue
		var head := PanelContainer.new()
		head.add_theme_stylebox_override("panel", HudStyle.box(GameData.factions[code].color.darkened(0.3), GameData.factions[code].color, 1))
		head.add_child(HudStyle.label(GameData.factions[code].name, 12, Color.WHITE))
		_detail.add_child(head)
		var flow := HFlowContainer.new()
		flow.add_theme_constant_override("h_separation", 2)
		flow.add_theme_constant_override("v_separation", 2)
		_detail.add_child(flow)
		var units: Array = by_owner[code]
		units.sort_custom(_unit_before)
		for u in units:
			var tile := UnitTile.make(u, GameStore.in_transport_form(tid, u))
			tile.button_pressed = _selected_units.has(int(u["unit_id"]))
			tile.toggled.connect(_on_unit_toggled.bind(int(u["unit_id"])))
			flow.add_child(tile)


## Display order within a faction: by unit type, promoted first, most XP first.
func _unit_before(a: Dictionary, b: Dictionary) -> bool:
	var types: Array = UnitIcons.FILES.keys()
	var ta := types.find(a["unit_type"])
	var tb := types.find(b["unit_type"])
	if ta != tb:
		return ta < tb
	if a.get("promoted", false) != b.get("promoted", false):
		return a.get("promoted", false)
	if a.get("xp", 0) != b.get("xp", 0):
		return a.get("xp", 0) > b.get("xp", 0)
	return a["unit_id"] < b["unit_id"]


func _on_unit_toggled(on: bool, unit_id: int) -> void:
	if on:
		_selected_units[unit_id] = true
	else:
		_selected_units.erase(unit_id)
	units_selected.emit(_selected_units.keys())


func _territory_field(tid: int, key: String) -> Variant:
	if GameStore.state.is_empty():
		return null
	var t = GameStore.state["territories"].get(str(tid))
	return null if t == null else t.get(key)
