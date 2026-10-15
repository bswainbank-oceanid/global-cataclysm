class_name GameOverReportPanel
extends Control
## The end-of-game report: one row per seat (server/report.py's build_game_report),
## already sorted by Victory status, then Strategic Centers, Territory MPC, Units
## produced, Units destroyed. Opens automatically when the game ends. It does NOT dim
## or block the map -- the player may still want to look at the final board -- and it
## can be minimized to a small reopenable tab and restored at will (GameStore's
## game_over_report_minimized), so the player can put it aside and bring it back as
## they review the final state.

const COLUMNS := [
	["Faction", 70], ["Victory status", 130], ["Elimination reason", 130], ["Eliminated by", 90],
	["Round eliminated", 100], ["SCs", 44], ["Territory MPC", 100], ["Units produced", 100],
	["Units destroyed", 100], ["Seat", 64], ["Bot type", 80], ["Bot strategy", 90],
	["Alliance strategy", 110], ["Alliance behavior", 110], ["Alliance history", 260],
]

var _panel: PanelContainer
var _grid: VBoxContainer  # the header, then one framed row per faction
var _row: HBoxContainer  # the row being filled (_add_row)
var _tab: Button  # the minimized state's reopen affordance
var _title: Label


func _ready() -> void:
	z_index = 380
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)

	var center := CenterContainer.new()
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(center)

	_panel = PanelContainer.new()
	_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	HudStyle.paper_sheet(_panel)
	_panel.custom_minimum_size = Vector2(900, 0)
	center.add_child(_panel)

	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_panel.add_child(v)

	var head := HBoxContainer.new()
	_title = HudStyle.label("Game Over - Final Report", 18, HudStyle.GOLD)
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_title)
	var minimize := Button.new()
	minimize.text = "Minimize"
	HudStyle.secondary(minimize)
	minimize.tooltip_text = "Put the report aside; a small tab reopens it"
	minimize.pressed.connect(func(): GameStore.toggle_game_over_report_minimized())
	head.add_child(minimize)
	v.add_child(head)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(880, 420)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	v.add_child(scroll)
	_grid = VBoxContainer.new()
	_grid.add_theme_constant_override("separation", 4)
	scroll.add_child(_grid)

	_tab = Button.new()
	_tab.text = "Game Over Report  ▲"
	HudStyle.primary(_tab)
	_tab.mouse_filter = Control.MOUSE_FILTER_STOP
	_tab.custom_minimum_size = Vector2(200, 36)
	_tab.tooltip_text = "Reopen the Game Over report"
	_tab.pressed.connect(func(): GameStore.toggle_game_over_report_minimized())
	_tab.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_tab.position -= Vector2(16, 16)
	add_child(_tab)

	GameStore.game_over_report_changed.connect(_sync)
	Stepper.game_reset.connect(func(): visible = false)
	_sync()


func _sync() -> void:
	var report: Array = GameStore.game_over_report
	visible = not report.is_empty()
	if not visible:
		return
	var minimized: bool = GameStore.game_over_report_minimized
	_panel.visible = not minimized
	_tab.visible = minimized
	if Dbg.args.has("shot"):
		print("[dbg] game over report: minimized=%s" % minimized)
	if minimized:
		return
	_rebuild(report)


func _rebuild(report: Array) -> void:
	# rounds_in_game is the SAME value on every row (server/report.py) -- shown once, in the title,
	# rather than as a repeated column.
	var rounds = report[0].get("rounds_in_game") if not report.is_empty() else null
	_title.text = "Game Over - Final Report (%d rounds)" % int(rounds) if rounds != null else "Game Over - Final Report"
	for c in _grid.get_children():
		_grid.remove_child(c)
		c.queue_free()
	# The header lines up with the rows' cells: the same inset as a row's frame.
	var head := MarginContainer.new()
	for side in ["left", "right"]:
		head.add_theme_constant_override("margin_" + side, ROW_INSET)
	_row = _row_hbox()
	head.add_child(_row)
	_grid.add_child(head)
	for col in _columns():
		_row.add_child(HudStyle.label(str(col[0]), 12, HudStyle.GOLD))
	_fit_columns()
	for row in report:
		_add_row(row)


const ROW_INSET := 8  # a row frame's border plus its padding, each side


static func _row_hbox() -> HBoxContainer:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 10)
	return h


## Gives the row's cells their columns' widths, so every row's columns line up.
func _fit_columns() -> void:
	var columns := _columns()
	var cells := _row.get_children()
	for i in mini(cells.size(), columns.size()):
		var c: Control = cells[i]
		c.custom_minimum_size.x = float(columns[i][1])
		c.size_flags_horizontal = Control.SIZE_FILL
		if c is Label:
			c.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART


func _add_row(row: Dictionary) -> void:
	var code := str(row.get("faction", ""))
	var col: Color = GameData.factions[code].color if GameData.factions.has(code) else HudStyle.TEXT
	# Each faction's entries framed together, in its colour.
	var frame := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(col, 0.06)
	sb.border_color = Color(col, 0.85)
	sb.set_border_width_all(2)
	sb.set_corner_radius_all(4)
	sb.content_margin_left = ROW_INSET - 2
	sb.content_margin_right = ROW_INSET - 2
	sb.content_margin_top = 4
	sb.content_margin_bottom = 4
	frame.add_theme_stylebox_override("panel", sb)
	_grid.add_child(frame)
	_row = _row_hbox()
	frame.add_child(_row)
	var own := HBoxContainer.new()
	own.add_child(_faction_box(code))
	_row.add_child(own)
	_cell(_or_dash(row.get("victory_status")), col)
	var reasons: Array = row.get("elimination_reason", []) if row.get("elimination_reason") != null else []
	_cell(", ".join(reasons) if not reasons.is_empty() else "-", HudStyle.TEXT_DIM)
	_cell(_or_dash(row.get("eliminated_by")), HudStyle.TEXT_DIM)
	_cell(_int_or_dash(row.get("round_eliminated")), HudStyle.TEXT_DIM)
	_cell(str(int(row.get("strategic_centers", 0))))
	_cell(str(int(row.get("territory_mpc", 0))))
	_cell(str(int(row.get("units_produced", 0))))
	_cell(str(int(row.get("units_destroyed", 0))))
	_cell(_or_dash(row.get("seat_type")))
	if Account.sees_bot_details():  # (which AI a bot played: admins only)
		_cell(_or_dash(row.get("bot_type")))
	_cell(_or_dash(row.get("bot_strategy")))
	_cell(_or_dash(row.get("alliance_strategy")))
	_cell(_or_dash(row.get("alliance_behavior")))
	var hist: Array = row.get("alliance_history", [])
	if hist.is_empty():
		_cell("-", HudStyle.TEXT_DIM)
	else:
		var lines := VBoxContainer.new()
		lines.add_theme_constant_override("separation", 3)
		for line in hist:
			lines.add_child(_history_line(str(line)))
		_row.add_child(lines)
	_fit_columns()


## The report's columns: Bot type only for admins (Account.sees_bot_details).
static func _columns() -> Array:
	return COLUMNS.filter(func(c): return c[0] != "Bot type" or Account.sees_bot_details())


func _or_dash(v) -> String:
	return "-" if v == null else str(v)


## Like _or_dash, but for a value that's genuinely a whole number (round_eliminated) --
## JSON.parse_string() decodes every JSON number as a float, so a plain str(v) would show
## "3.0" rather than "3" without this cast.
func _int_or_dash(v) -> String:
	return "-" if v == null else str(int(v))


## One line of the alliance record ("R5: joined with UE", "R7: left (UE, NAA)", "R9: UE left"), each faction
## named in it shown as its faction box, so who joined or left whom reads at a glance.
func _history_line(line: String) -> Control:
	var h := HFlowContainer.new()
	h.add_theme_constant_override("h_separation", 4)
	h.add_theme_constant_override("v_separation", 2)
	var words := RegEx.create_from_string("\\b[A-Z]{2,4}\\b")
	var at := 0
	for m in words.search_all(line):
		if not GameData.factions.has(m.get_string()):
			continue
		_text_bit(h, line.substr(at, m.get_start() - at))
		h.add_child(_faction_box(m.get_string()))
		at = m.get_end()
	_text_bit(h, line.substr(at))
	return h


func _text_bit(into: Container, text: String) -> void:
	text = text.strip_edges()
	if text != "":
		var l := HudStyle.label(text, 12, HudStyle.TEXT_DIM)
		l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		into.add_child(l)


## A faction's box: its code, white on its own colour, its name as the tooltip.
static func _faction_box(code: String) -> Control:
	var box := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = GameData.factions[code].color if GameData.factions.has(code) else HudStyle.TEXT_DIM
	sb.set_corner_radius_all(3)
	sb.content_margin_left = 5
	sb.content_margin_right = 5
	sb.content_margin_top = 1
	sb.content_margin_bottom = 1
	box.add_theme_stylebox_override("panel", sb)
	box.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	box.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	box.tooltip_text = str(GameData.factions[code].name) if GameData.factions.has(code) else code
	box.mouse_filter = Control.MOUSE_FILTER_PASS
	var l := HudStyle.label(code, 12, Color.WHITE)
	l.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.6))
	l.add_theme_constant_override("outline_size", 3)
	box.add_child(l)
	return box


func _cell(text: String, colour: Color = HudStyle.TEXT) -> void:
	var l := HudStyle.label(text, 12, colour)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_row.add_child(l)
