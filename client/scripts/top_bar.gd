class_name TopBar
extends PanelContainer
## Top strip from the mockup: a title / round / acting-faction / phase block
## on the left, then one stats panel per active faction (T = territories,
## MCP = treasury, SC = strategic centers, UV = deployed unit value, plus
## allies). Rebuilt whenever GameStore's state changes; the acting
## faction's panel gets a gold edge.

var _row: HBoxContainer


func _ready() -> void:
	add_theme_stylebox_override("panel", HudStyle.box(HudStyle.EDGE, Color(0.06, 0.075, 0.1)))
	_row = HBoxContainer.new()
	_row.add_theme_constant_override("separation", 8)
	add_child(_row)
	GameStore.state_changed.connect(_rebuild)
	_rebuild()


func _rebuild() -> void:
	for c in _row.get_children():
		_row.remove_child(c)  # detach now: a queued free would leave stale and new panels laid out together for a frame
		c.queue_free()
	_row.add_child(_title_block())
	for code in GameStore.seated_factions():
		_row.add_child(_faction_panel(code))


func _title_block() -> Control:
	var p := PanelContainer.new()
	p.custom_minimum_size = Vector2(190, 0)
	p.add_theme_stylebox_override("panel", HudStyle.box())
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 1)
	p.add_child(v)
	v.add_child(HudStyle.label("Global Cataclysm: 1972", 15, HudStyle.GOLD))
	if GameStore.state.is_empty():
		v.add_child(HudStyle.label("waiting for game...", 12, HudStyle.TEXT_DIM))
		return p
	var active := str(GameStore.state.get("active_faction", ""))
	if GameStore.state.get("game_over", false):
		v.add_child(HudStyle.label("GAME OVER", 15, Color(1.0, 0.45, 0.4)))
		return p
	v.add_child(HudStyle.label("Round %d" % GameStore.round_number(), 13))
	if active != "":
		var seat := GameStore.seat_label(active)
		v.add_child(HudStyle.label("%s - %s" % [active, seat], 13, GameData.factions[active].color.lightened(0.35)))
	v.add_child(HudStyle.label(GameStore.phase_label(), 13, HudStyle.TEXT))
	return p


func _faction_panel(code: String) -> Control:
	var eliminated: bool = GameStore.faction_state(code).get("eliminated", false)
	var is_active := code == str(GameStore.state.get("active_faction", "")) and not eliminated
	var col: Color = GameData.factions[code].color
	var p := PanelContainer.new()
	p.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	p.custom_minimum_size = Vector2(170, 0)
	p.tooltip_text = GameData.factions[code].name
	p.add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD if is_active else HudStyle.EDGE, HudStyle.BG, 2 if is_active else 1))
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 2)
	p.add_child(v)

	var head := HBoxContainer.new()
	var seat := HudStyle.label(GameStore.seat_label(code), 13)
	seat.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(seat)
	var tag := PanelContainer.new()
	tag.add_theme_stylebox_override("panel", HudStyle.box(col.darkened(0.3), col, 1))
	tag.add_child(HudStyle.label(code, 13, Color.WHITE))
	head.add_child(tag)
	v.add_child(head)

	var grid := GridContainer.new()
	grid.columns = 4
	grid.add_theme_constant_override("h_separation", 10)
	grid.add_theme_constant_override("v_separation", 0)
	for h in ["T", "MCP", "SC", "UV"]:
		grid.add_child(HudStyle.label(h, 11, HudStyle.TEXT_DIM))
	var f := GameStore.faction_state(code)
	for val in [GameStore.territory_count(code), int(f["treasury_mpc"]), GameStore.sc_count(code), GameStore.unit_value(code)]:
		grid.add_child(HudStyle.label(str(val), 14))
	v.add_child(grid)

	var allies := GameStore.allies_of(code)
	if eliminated:
		v.add_child(HudStyle.label("ELIMINATED", 11, Color(1.0, 0.45, 0.4)))
		p.modulate = Color(1, 1, 1, 0.55)
	else:
		v.add_child(HudStyle.label("Allies: " + (", ".join(allies) if not allies.is_empty() else "none"), 11, HudStyle.TEXT_DIM))
	return p
