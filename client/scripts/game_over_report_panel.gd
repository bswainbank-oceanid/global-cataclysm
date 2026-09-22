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
var _grid: GridContainer
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
	_panel.add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD, Color(0.07, 0.085, 0.11), 3))
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
	minimize.focus_mode = Control.FOCUS_NONE
	minimize.tooltip_text = "Put the report aside; a small tab reopens it"
	minimize.pressed.connect(func(): GameStore.toggle_game_over_report_minimized())
	head.add_child(minimize)
	v.add_child(head)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(880, 420)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	v.add_child(scroll)
	_grid = GridContainer.new()
	_grid.columns = COLUMNS.size()
	_grid.add_theme_constant_override("h_separation", 10)
	_grid.add_theme_constant_override("v_separation", 4)
	scroll.add_child(_grid)

	_tab = Button.new()
	_tab.text = "Game Over Report  ▲"
	_tab.focus_mode = Control.FOCUS_NONE
	_tab.mouse_filter = Control.MOUSE_FILTER_STOP
	_tab.custom_minimum_size = Vector2(200, 36)
	_tab.tooltip_text = "Reopen the Game Over report"
	_tab.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	_tab.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.24, 0.2, 0.06), 2))
	_tab.add_theme_color_override("font_color", HudStyle.GOLD)
	_tab.add_theme_color_override("font_hover_color", Color.WHITE)
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
	for col in COLUMNS:
		var h := HudStyle.label(str(col[0]), 12, HudStyle.GOLD)
		h.custom_minimum_size = Vector2(float(col[1]), 0)
		_grid.add_child(h)
	for row in report:
		_add_row(row)


func _add_row(row: Dictionary) -> void:
	var code := str(row.get("faction", ""))
	var col: Color = GameData.factions[code].color.lightened(0.3) if GameData.factions.has(code) else HudStyle.TEXT
	_cell(code, col)
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
	_cell(_or_dash(row.get("bot_type")))
	_cell(_or_dash(row.get("bot_strategy")))
	_cell(_or_dash(row.get("alliance_strategy")))
	_cell(_or_dash(row.get("alliance_behavior")))
	var hist: Array = row.get("alliance_history", [])
	_cell("\n".join(hist) if not hist.is_empty() else "-", HudStyle.TEXT_DIM)


func _or_dash(v) -> String:
	return "-" if v == null else str(v)


## Like _or_dash, but for a value that's genuinely a whole number (round_eliminated) --
## JSON.parse_string() decodes every JSON number as a float, so a plain str(v) would show
## "3.0" rather than "3" without this cast.
func _int_or_dash(v) -> String:
	return "-" if v == null else str(int(v))


func _cell(text: String, colour: Color = HudStyle.TEXT) -> void:
	var l := HudStyle.label(text, 12, colour)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_grid.add_child(l)
