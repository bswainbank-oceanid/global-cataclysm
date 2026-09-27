class_name LaunchScreen
extends Control
## The game launch screen: the scenario -- Global Cataclysm: 1972 as it stands, or a New
## scenario dealt afresh by the server's generator (engine/scenario_generator.py) -- and six
## seats, each a Human, Bot, Neutral or Noncombatant (a new scenario: Human, Bot or Not
## playing), with for players (humans and bots) a faction (or random) and a starting
## alliance, and for bots an alliance strategy and behavior. A new scenario also sets, per
## player seat, its territory value, initial MPC, units MPC, promotions and Strategic
## Centers, with a Neutral row for the Neutral pool, plus the scenario-wide options below.
## Bots always play the heuristic (strategy) AI. "Start Game" sends the settings to the
## server (server/lobby.py builds the game and re-checks everything); "Resume" goes back to a
## game already running there.
##
## The seat table: Seat, Type and Faction stay put; the other columns scroll sideways when
## the window is too narrow for them.
##
## The rules mirrored here for instant feedback: at least two players, at most one
## human, each faction picked once, a starting alliance needs two or more members and
## can't include every player, and a seat's initial MPC is at least its units MPC.

signal start_requested(settings: Dictionary)
signal resume_requested

var SEATS := 6  # one seat per faction: set from GameData's faction set in _ready
const MODES := [["Human", "HUMAN"], ["Bot", "BOT"], ["Neutral", "NEUTRAL"], ["Noncombatant", "NONCOMBATANT"]]
const NEW_MODES := [["Human", "HUMAN"], ["Bot", "BOT"], ["Not playing", "NOT_PLAYING"]]  # a new scenario's seats
const SCENARIOS := [["Global Cataclysm: 1972", "fixed"], ["New scenario", "new"]]
const ALLIANCES := ["None", "Alliance 1", "Alliance 2", "Alliance 3"]
const STRATEGIES := ["random", "aggressive", "passive", "counterweight", "independent", "variable"]
const BEHAVIORS := ["random", "loyal", "opportunistic", "treacherous", "variable"]
# A new scenario's seat settings: [key, header, width]; the Neutral row has no initial MPC.
const SEAT_KEYS := [["territory_value", "Territory", 84], ["initial_mpc", "Initial MPC", 84], ["units_mpc", "Units MPC", 84],
	["promotions", "Promotions", 84], ["scs", "SCs", 70]]
const NEUTRAL_KEYS := ["territory_value", "units_mpc", "promotions", "scs"]
const ROW_H := 30.0
const HEAD_H := 18.0
const PATH := "user://launch.cfg"

var _rows: Array = []  # per seat: {mode, faction, chip, alliance, strategy, behavior, <seat key>: SpinBox}
var _neutral_row := {}  # the Neutral row's SpinBoxes by key
var _neutral_cells: Array = []  # every cell of the Neutral row (shown for a new scenario only)
var _new_columns: Array = []  # the new scenario's seat-setting columns
var _territory_auto := {}  # seat index (or "neutral") -> true while its territory value follows the default
var _updating := false  # setting SpinBoxes from code: not the player's edit
var _seat_specs := {}  # from the server: {total, players: [...], neutral: [...]}
var _scroll: ScrollContainer
var _fixed: HBoxContainer  # the Seat, Type and Faction columns
var _scenario: OptionButton
var _new_panel: GridContainer  # a new scenario's scenario-wide options
var _territory_note: Label
var _option_specs: Array = []  # from the server: [{key, label, kind, min, max, default, help}]
var _option_controls := {}  # key -> SpinBox | CheckBox
var _option_values := {}  # key -> the value last chosen (kept across the server's refreshes)
var _seat_values := {}  # "<seat>/<key>" -> remembered seat setting (applied when the server's specs arrive)
var _randomize: CheckBox
var _can_withdraw: CheckBox
var _can_rejoin: CheckBox
var _max_alliance: OptionButton
var _combat_first: CheckBox
var _noncombat_first: CheckBox
var _max_pref := 3  # the maximum alliance size the player asked for
var _max_size := 3  # ...as offered: kept within 2 .. players-1 for the players there are
var _message: Label
var _start: Button
var _resume: Button
var _game_running := false
var _loading := false
var remember := true  # keep the last setup in user://launch.cfg (off for scripted runs and tests)


func _ready() -> void:
	SEATS = GameData.faction_order.size()
	z_index = 500
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var bg := ColorRect.new()
	bg.color = Color(0.045, 0.06, 0.085)
	add_child(bg)
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD, Color(0.07, 0.085, 0.11), 2))
	center.add_child(panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	panel.add_child(v)

	var title := HudStyle.label("Global Cataclysm: 1972", 26, HudStyle.GOLD)
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(title)
	var scenario_row := HBoxContainer.new()
	scenario_row.alignment = BoxContainer.ALIGNMENT_CENTER
	scenario_row.add_theme_constant_override("separation", 10)
	scenario_row.add_child(HudStyle.label("New game:", 15, HudStyle.TEXT_DIM))
	_scenario = _option(SCENARIOS.map(func(s): return s[0]), 240)
	_scenario.item_selected.connect(func(_i): _scenario_changed())
	scenario_row.add_child(_scenario)
	v.add_child(scenario_row)
	v.add_child(HSeparator.new())

	_build_seat_table(v)
	_territory_note = HudStyle.label("", 12, HudStyle.TEXT_DIM)
	_territory_note.visible = false
	v.add_child(_territory_note)

	_new_panel = GridContainer.new()
	_new_panel.columns = 6
	_new_panel.add_theme_constant_override("h_separation", 10)
	_new_panel.add_theme_constant_override("v_separation", 4)
	_new_panel.visible = false
	v.add_child(_new_panel)

	_randomize = _checkbox("Randomize turn order", true, v)
	_can_withdraw = _checkbox("Players can withdraw from alliances", true, v)
	_can_rejoin = _checkbox("Players can rejoin alliances they left", false, v)
	_combat_first = _checkbox("Combat Moves allowed on a faction's first turn", false, v)
	_noncombat_first = _checkbox("Non-Combat Moves allowed on a faction's first turn", true, v)
	var size_row := HBoxContainer.new()
	size_row.add_theme_constant_override("separation", 10)
	size_row.add_child(HudStyle.label("Maximum alliance size", 14))
	_max_alliance = _option([], 70)
	_max_alliance.item_selected.connect(func(i: int):
		_max_pref = int(_max_alliance.get_item_text(i))
		_max_size = _max_pref
		_changed())
	_max_alliance.tooltip_text = "The most factions one alliance may hold, from 1 to the number of players minus one. 1 means no alliances at all, and no Alliances phase."
	size_row.add_child(_max_alliance)
	v.add_child(size_row)

	_message = HudStyle.label("", 12, Color(1.0, 0.6, 0.5))
	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_message.custom_minimum_size = Vector2(760, 34)
	v.add_child(_message)

	var buttons := HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_CENTER
	buttons.add_theme_constant_override("separation", 14)
	v.add_child(buttons)
	_resume = _button("Resume current game")
	_resume.pressed.connect(func(): resume_requested.emit())
	buttons.add_child(_resume)
	_start = _button("Start Game")
	_start.pressed.connect(func():
		_save()
		start_requested.emit(settings()))
	buttons.add_child(_start)

	get_viewport().size_changed.connect(_fit_scroll)
	_load()
	_changed()


# ---- building ------------------------------------------------------------------------------------

## The seat table as columns (so a whole column can come and go): Seat, Type and Faction fixed,
## the rest in a sideways-scrolling area; one cell per seat, and a last one for the Neutral row.
func _build_seat_table(parent: VBoxContainer) -> void:
	var table := HBoxContainer.new()
	table.add_theme_constant_override("separation", 10)
	parent.add_child(table)
	var fixed := HBoxContainer.new()
	fixed.add_theme_constant_override("separation", 10)
	table.add_child(fixed)
	_fixed = fixed
	_scroll = ScrollContainer.new()
	_scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	table.add_child(_scroll)
	var moving := HBoxContainer.new()
	moving.add_theme_constant_override("separation", 10)
	_scroll.add_child(moving)

	for i in SEATS:
		_rows.append({})
	var seat_col := _column(fixed, "Seat", 30)
	var type_col := _column(fixed, "Type", 130)
	var faction_col := _column(fixed, "Faction", 250)
	var chip_col := _column(fixed, "", 14)
	var alliance_col := _column(moving, "Starting alliance", 120)
	var setting_cols := {}
	for k in SEAT_KEYS:
		setting_cols[k[0]] = _column(moving, k[1], k[2])
		_new_columns.append(setting_cols[k[0]])
	var strategy_col := _column(moving, "Alliance strategy", 140)
	var behavior_col := _column(moving, "Alliance behavior", 150)

	for i in SEATS:
		var row: Dictionary = _rows[i]
		seat_col.add_child(_cell_label("%d" % (i + 1), 15, HudStyle.GOLD, 30))
		var mode := _option(MODES.map(func(m): return m[0]), 130)
		mode.select(0 if i == 0 else 1 if i == 1 else 3)  # a Human, a Bot, the rest Noncombatant
		type_col.add_child(mode)
		row["mode"] = mode
		var faction := _option(["Random"], 250)
		for code in GameData.faction_order:
			faction.add_item("%s  -  %s" % [code, GameData.factions[code].name])
			faction.set_item_metadata(faction.item_count - 1, code)
		faction_col.add_child(faction)
		row["faction"] = faction
		var chip := ColorRect.new()
		chip.custom_minimum_size = Vector2(14, ROW_H)
		chip_col.add_child(chip)
		row["chip"] = chip
		row["alliance"] = _option(ALLIANCES, 120)
		alliance_col.add_child(row["alliance"])
		for k in SEAT_KEYS:
			var sb := _spin(k[2], str(k[0]), i)
			setting_cols[k[0]].add_child(sb)
			row[k[0]] = sb
		row["strategy"] = _option(STRATEGIES.map(func(s): return str(s).capitalize()), 140)
		strategy_col.add_child(row["strategy"])
		row["behavior"] = _option(BEHAVIORS.map(func(s): return str(s).capitalize()), 150)
		behavior_col.add_child(row["behavior"])
		_territory_auto[i] = true

	# the Neutral row (a new scenario's Neutral pool)
	_neutral_cells = [_cell_label("N", 15, GameData.neutral_color.lightened(0.3), 30),
		_cell_label("Neutral", 15, GameData.neutral_color.lightened(0.3), 130),
		_cell_label("the Neutral pool", 13, HudStyle.TEXT_DIM, 250), _spacer(14), _spacer(120)]
	seat_col.add_child(_neutral_cells[0])
	type_col.add_child(_neutral_cells[1])
	faction_col.add_child(_neutral_cells[2])
	chip_col.add_child(_neutral_cells[3])
	alliance_col.add_child(_neutral_cells[4])
	for k in SEAT_KEYS:
		var cell: Control
		if NEUTRAL_KEYS.has(k[0]):
			var sb := _spin(k[2], str(k[0]), -1)
			_neutral_row[k[0]] = sb
			cell = sb
		else:
			cell = _spacer(k[2])
		setting_cols[k[0]].add_child(cell)
		_neutral_cells.append(cell)
	for col in [strategy_col, behavior_col]:
		var cell := _spacer(140 if col == strategy_col else 150)
		col.add_child(cell)
		_neutral_cells.append(cell)
	_territory_auto["neutral"] = true
	_set_new_visible(false)


func _column(parent: HBoxContainer, header: String, width: float) -> VBoxContainer:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	col.custom_minimum_size = Vector2(width, 0)
	parent.add_child(col)
	var h := HudStyle.label(header, 12, HudStyle.TEXT_DIM)
	h.custom_minimum_size = Vector2(width, HEAD_H)
	col.add_child(h)
	return col


func _cell_label(text: String, size: int, color: Color, width: float) -> Label:
	var l := HudStyle.label(text, size, color)
	l.custom_minimum_size = Vector2(width, ROW_H)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	return l


func _spacer(width: float) -> Control:
	var c := Control.new()
	c.custom_minimum_size = Vector2(width, ROW_H)
	return c


## A seat setting's SpinBox. `seat`: its seat index, or -1 for the Neutral row.
func _spin(width: float, key: String, seat: int) -> SpinBox:
	var sb := SpinBox.new()
	sb.custom_minimum_size = Vector2(width, ROW_H)
	sb.min_value = 0
	sb.max_value = 1000
	sb.step = 1
	sb.value_changed.connect(func(_x: float): _seat_setting_changed(seat, key))
	return sb


func _checkbox(text: String, on: bool, parent: VBoxContainer) -> CheckBox:
	var c := CheckBox.new()
	c.text = text
	c.button_pressed = on
	c.focus_mode = Control.FOCUS_NONE
	_check_style(c)
	c.toggled.connect(func(_on): _changed())
	parent.add_child(c)
	return c


func _check_style(c: CheckBox) -> void:
	HudStyle.style_checkbox(c)


func _button(text: String) -> Button:
	var b := Button.new()
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(220, 44)
	b.add_theme_font_size_override("font_size", 15)
	b.add_theme_color_override("font_color", HudStyle.GOLD)
	b.add_theme_color_override("font_hover_color", Color.WHITE)
	b.add_theme_color_override("font_disabled_color", HudStyle.TEXT_DIM)
	b.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	b.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.24, 0.2, 0.06), 2))
	b.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, Color(0.3, 0.25, 0.08), 2))
	b.add_theme_stylebox_override("disabled", HudStyle.box(HudStyle.EDGE, HudStyle.BG, 1))
	return b


func _option(items: Array, width: float) -> OptionButton:
	var o := OptionButton.new()
	o.focus_mode = Control.FOCUS_NONE
	o.custom_minimum_size = Vector2(width, ROW_H)
	for it in items:
		o.add_item(str(it))
	o.item_selected.connect(func(_i): _changed())
	return o


## The scrolling columns take what room the window leaves beside the fixed ones.
func _fit_scroll() -> void:
	if _scroll == null or not is_inside_tree():
		return
	var content: Control = _scroll.get_child(0)
	var want := content.get_combined_minimum_size().x
	# (the panel's border and padding, and the gap between the fixed and scrolling columns)
	var room := get_viewport_rect().size.x - _fixed.get_combined_minimum_size().x - 110.0
	_scroll.custom_minimum_size = Vector2(clampf(want, 200.0, maxf(200.0, room)), content.get_combined_minimum_size().y + 12.0)


# ---- reading the screen -----------------------------------------------------------

func _is_new() -> bool:
	return _scenario != null and _scenario.selected == 1


func _modes() -> Array:
	return NEW_MODES if _is_new() else MODES


func _mode_of(row: Dictionary) -> String:
	var modes := _modes()
	var i: int = (row["mode"] as OptionButton).selected
	return modes[clampi(i, 0, modes.size() - 1)][1]


func _faction_of(row: Dictionary) -> String:
	var o: OptionButton = row["faction"]
	return "random" if o.selected == 0 else str(o.get_item_metadata(o.selected))


func _players() -> int:
	var n := 0
	for row in _rows:
		var mode := _mode_of(row)
		if mode == "HUMAN" or mode == "BOT":
			n += 1
	return n


func settings() -> Dictionary:
	var seats := []
	for i in SEATS:
		var row: Dictionary = _rows[i]
		var mode := _mode_of(row)
		var player: bool = mode == "HUMAN" or mode == "BOT"
		var seat := {
			"mode": mode,
			"faction": _faction_of(row),
			"alliance": (row["alliance"] as OptionButton).selected if player else 0,
			"strategy": STRATEGIES[(row["strategy"] as OptionButton).selected],
			"behavior": BEHAVIORS[(row["behavior"] as OptionButton).selected],
		}
		if _is_new() and player:
			for k in SEAT_KEYS:
				if k[0] == "territory_value" and _territory_auto.get(i, true):
					continue  # the default: the server splits the map among the players
				seat[k[0]] = int((row[k[0]] as SpinBox).value)
		seats.append(seat)
	var s := {"seats": seats, "randomize_order": _randomize.button_pressed,
		"can_withdraw": _can_withdraw.button_pressed, "can_rejoin": _can_rejoin.button_pressed}
	s["max_alliance_size"] = _max_size
	if _is_new():
		var neutral := {}
		for k in NEUTRAL_KEYS:
			if k == "territory_value" and _territory_auto.get("neutral", true):
				continue  # the default: whatever the players leave
			neutral[k] = int((_neutral_row[k] as SpinBox).value)
		s["scenario"] = {"kind": "new", "options": new_options(), "neutral": neutral}
	s["allow_combat_first_turn"] = _combat_first.button_pressed
	s["allow_noncombat_first_turn"] = _noncombat_first.button_pressed
	if Dbg.args.has("combat_first_turn"):
		s["dev"] = {"combat_first_turn": true}  # scripted runs only: the rules skip it
	return s


## The reasons this setup can't start (empty = it can). Mirrors server/lobby.py.
func problems() -> Array:
	var out := []
	var players := 0
	var humans := 0
	var groups := {}
	for i in SEATS:
		var mode := _mode_of(_rows[i])
		if mode == "HUMAN":
			humans += 1
		if mode == "HUMAN" or mode == "BOT":
			players += 1
			var a: int = (_rows[i]["alliance"] as OptionButton).selected
			if a > 0:
				groups[a] = int(groups.get(a, 0)) + 1
	if players < 2:
		out.append("At least two players (humans or bots) are needed.")
	if humans > 1:
		out.append("At most one human player.")
	for a in groups:
		if groups[a] > _max_size and players >= 2:
			out.append("Alliance %d has %d members, more than the maximum alliance size (%d)." % [a, groups[a], _max_size])
		elif groups[a] < 2:
			out.append("Alliance %d has only one member: an alliance needs two or more." % a)
		elif groups[a] >= players and players >= 2:
			out.append("Alliance %d would contain every player, which ends the game at once." % a)
	return out


# ---- keeping the controls consistent --------------------------------------------------

func _changed() -> void:
	if _loading:
		return
	_refresh_max_alliance()  # (first: the alliance choices below depend on the size)
	var human_seat := -1
	var taken := {}
	for i in SEATS:
		if _mode_of(_rows[i]) == "HUMAN" and human_seat < 0:
			human_seat = i
		var f := _faction_of(_rows[i])
		if f != "random":
			taken[f] = i
	for i in SEATS:
		var row: Dictionary = _rows[i]
		var mode := _mode_of(row)
		var mode_o: OptionButton = row["mode"]
		mode_o.set_item_disabled(0, human_seat >= 0 and human_seat != i)  # at most one human
		var fac_o: OptionButton = row["faction"]
		for k in range(1, fac_o.item_count):
			var code := str(fac_o.get_item_metadata(k))
			fac_o.set_item_disabled(k, taken.has(code) and taken[code] != i)
		var f := _faction_of(row)
		(row["chip"] as ColorRect).color = GameData.factions[f].color if f != "random" else Color(0.3, 0.34, 0.4)
		var player: bool = mode == "HUMAN" or mode == "BOT"
		(row["alliance"] as OptionButton).disabled = not player or _max_size < 2
		if _max_size < 2:
			(row["alliance"] as OptionButton).select(0)
		(row["strategy"] as OptionButton).disabled = mode != "BOT"
		(row["behavior"] as OptionButton).disabled = mode != "BOT"
		for k in SEAT_KEYS:
			(row[k[0]] as SpinBox).editable = player
			(row[k[0]] as SpinBox).modulate.a = 1.0 if player else 0.35
	_can_rejoin.disabled = not _can_withdraw.button_pressed  # nobody leaves, so nobody rejoins
	_refresh_territory()
	var p := problems()
	_message.text = "\n".join(p) if not p.is_empty() else ""
	_start.disabled = not p.is_empty()
	_resume.visible = _game_running
	_fit_scroll.call_deferred()


## Show the screen (again). `game_running`: the server has a game to go back to.
## `new_scenario`: the server's New Scenario description ({"options": [...], "seats": {...}}).
func open(game_running: bool, new_scenario := {}) -> void:
	_game_running = game_running
	set_new_scenario_options(new_scenario.get("options", []))
	set_seat_specs(new_scenario.get("seats", {}))
	_changed()
	visible = true


func close() -> void:
	visible = false


## A server rejection of the settings: show its reasons.
func show_error(message: String) -> void:
	_message.text = message


## The size options are 2 .. players-1; the choice is kept (clamped) as players come and go.
func _refresh_max_alliance() -> void:
	var players := _players()
	_max_alliance.clear()
	var top := maxi(players - 1, 1)  # sizes 1 .. players-1; 1 = no alliances (and no Alliances phase)
	for n in range(1, top + 1):
		_max_alliance.add_item(str(n))
	_max_alliance.disabled = top == 1
	_max_size = clampi(_max_pref, 1, top)
	_max_alliance.select(_max_size - 1)


# ---- the scenario ---------------------------------------------------------------------

## "fixed" or "new" (scripted runs).
func select_scenario(kind: String) -> void:
	_scenario.select(1 if kind == "new" else 0)
	_scenario_changed()


## Switching between the fixed scenario and a new one: the seat types change (a Neutral or
## Noncombatant seat becomes Not playing, and back), and the new scenario's settings show.
func _scenario_changed() -> void:
	var was_loading := _loading
	_loading = true
	for row in _rows:
		var o: OptionButton = row["mode"]
		var old: int = o.selected
		o.clear()
		for m in _modes():
			o.add_item(m[0])
		if _is_new():
			o.select(mini(old, 2))  # Human, Bot, the rest Not playing
		else:
			o.select(old if old < 2 else 3)  # Not playing -> Noncombatant
	_loading = was_loading
	_set_new_visible(_is_new())
	_changed()


func _set_new_visible(on: bool) -> void:
	for col in _new_columns:
		col.visible = on
	for cell in _neutral_cells:
		cell.visible = on
	if _new_panel != null:
		_new_panel.visible = on
	if _territory_note != null:
		_territory_note.visible = on


## The seat settings' ranges and defaults, from the server; a seat keeps what was chosen before
## (this session, or remembered from the last launch), else takes the default.
func set_seat_specs(specs: Dictionary) -> void:
	if specs.is_empty() or _seat_specs == specs:
		return
	_seat_specs = specs
	_updating = true
	for spec in specs.get("players", []):
		var key := str(spec["key"])
		for i in SEATS:
			var sb: SpinBox = _rows[i][key]
			sb.min_value = float(spec["min"])
			sb.max_value = float(spec["max"])
			var remembered = _seat_values.get("%d/%s" % [i, key])
			if remembered != null:
				sb.value = float(remembered)
				if key == "territory_value":
					_territory_auto[i] = false
			elif spec["default"] != null:
				sb.value = float(spec["default"])
	for spec in specs.get("neutral", []):
		var key := str(spec["key"])
		var sb: SpinBox = _neutral_row[key]
		sb.min_value = float(spec["min"])
		sb.max_value = float(spec["max"])
		var remembered = _seat_values.get("neutral/%s" % key)
		if remembered != null:
			sb.value = float(remembered)
			if key == "territory_value":
				_territory_auto["neutral"] = false
		elif spec["default"] != null:
			sb.value = float(spec["default"])
	_updating = false
	_refresh_territory()


## A seat setting edited by the player: a territory value stops following the default, and a
## seat's initial MPC stays at least its units MPC (raising one, or lowering the other, to match).
func _seat_setting_changed(seat: int, key: String) -> void:
	if _updating:
		return
	var tag = seat if seat >= 0 else "neutral"
	if key == "territory_value":
		_territory_auto[tag] = false
	if seat >= 0:
		var row: Dictionary = _rows[seat]
		_updating = true
		if key == "units_mpc" and row["initial_mpc"].value < row["units_mpc"].value:
			row["initial_mpc"].value = row["units_mpc"].value
		elif key == "initial_mpc" and row["initial_mpc"].value < row["units_mpc"].value:
			row["units_mpc"].value = row["initial_mpc"].value
		_updating = false
	_refresh_territory()


## Territory values left to their defaults follow the players there are: the map's total split
## evenly among them, and the Neutral pool whatever they leave; and the note under the table
## says how the map is shared out.
func _refresh_territory() -> void:
	var total := int(_seat_specs.get("total", 0))
	if total <= 0 or not _is_new():
		return
	var players := _players()
	_updating = true
	var used := 0
	for i in SEATS:
		var row: Dictionary = _rows[i]
		var mode := _mode_of(row)
		if mode != "HUMAN" and mode != "BOT":
			continue
		if _territory_auto.get(i, true):
			row["territory_value"].value = float(total / maxi(1, players))
		used += int(row["territory_value"].value)
	if _territory_auto.get("neutral", true):
		_neutral_row["territory_value"].value = float(maxi(0, total - used))
	var neutral := int(_neutral_row["territory_value"].value)
	_updating = false
	var asked := used + neutral
	if asked > total:
		_territory_note.text = "Territory: %d asked for, %d on the map -- every seat's value will be scaled down to fit." % [asked, total]
	else:
		_territory_note.text = "Territory: players %d + Neutral %d of %d on the map; Noncombatant %d." % [used, neutral, total, total - asked]


## The generator's scenario-wide options, as the server describes them; the values chosen before
## (this session, or remembered from the last launch) are kept, else the server's defaults.
func set_new_scenario_options(specs: Array) -> void:
	if specs.is_empty() or _option_specs == specs:
		return
	_option_specs = specs
	for c in _new_panel.get_children():
		c.queue_free()
	_option_controls.clear()
	for spec in specs:
		var key := str(spec["key"])
		var label := HudStyle.label(str(spec["label"]), 12, HudStyle.TEXT_DIM)
		label.tooltip_text = str(spec.get("help", ""))
		label.mouse_filter = Control.MOUSE_FILTER_PASS
		_new_panel.add_child(label)
		var value = _option_values.get(key, spec["default"])
		if str(spec["kind"]) == "bool":
			var cb := CheckBox.new()
			cb.button_pressed = bool(value)
			cb.focus_mode = Control.FOCUS_NONE
			cb.tooltip_text = label.tooltip_text
			_check_style(cb)
			cb.toggled.connect(func(on: bool): _option_values[key] = on)
			_new_panel.add_child(cb)
			_option_controls[key] = cb
		else:
			var sb := SpinBox.new()
			sb.min_value = float(spec["min"])
			sb.max_value = float(spec["max"])
			sb.step = 1.0 if str(spec["kind"]) == "int" else 0.1
			sb.value = float(value) if value != null else float(spec["max"])
			sb.custom_minimum_size = Vector2(90, 26)
			sb.tooltip_text = label.tooltip_text
			sb.value_changed.connect(func(x: float): _option_values[key] = int(x) if str(spec["kind"]) == "int" else x)
			_new_panel.add_child(sb)
			_option_controls[key] = sb
		_option_values[key] = _control_value(key, spec)


func _control_value(key: String, spec: Dictionary):
	var c = _option_controls[key]
	if c is CheckBox:
		return (c as CheckBox).button_pressed
	var x: float = (c as SpinBox).value
	return int(x) if str(spec["kind"]) == "int" else x


## The new scenario's scenario-wide options as chosen: {key: value}.
func new_options() -> Dictionary:
	var out := {}
	for spec in _option_specs:
		var key := str(spec["key"])
		out[key] = _control_value(key, spec) if _option_controls.has(key) else _option_values.get(key, spec["default"])
	return out


# ---- remembering the last setup ---------------------------------------------------------

func _save() -> void:
	if Dbg.args.has("shot") or not remember:
		return
	var cfg := ConfigFile.new()
	var s := settings()
	cfg.set_value("launch", "randomize_order", s["randomize_order"])
	cfg.set_value("launch", "can_withdraw", s["can_withdraw"])
	cfg.set_value("launch", "can_rejoin", s["can_rejoin"])
	cfg.set_value("launch", "max_alliance_size", _max_pref)
	cfg.set_value("launch", "combat_first", _combat_first.button_pressed)
	cfg.set_value("launch", "noncombat_first", _noncombat_first.button_pressed)
	cfg.set_value("launch", "scenario", SCENARIOS[_scenario.selected][1])
	for key in new_options():
		cfg.set_value("new_scenario", key, new_options()[key])
	for i in SEATS:
		var row: Dictionary = _rows[i]
		for key in ["mode", "faction", "alliance", "strategy", "behavior"]:
			cfg.set_value("seat%d" % i, key, (row[key] as OptionButton).selected)
		for k in SEAT_KEYS:
			if k[0] == "territory_value" and _territory_auto.get(i, true):
				continue
			cfg.set_value("seat%d" % i, k[0], int((row[k[0]] as SpinBox).value))
	for key in NEUTRAL_KEYS:
		if key == "territory_value" and _territory_auto.get("neutral", true):
			continue
		cfg.set_value("neutral", key, int((_neutral_row[key] as SpinBox).value))
	cfg.save(PATH)


func _load() -> void:
	if Dbg.args.has("shot") or not remember:
		return
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK:
		return
	_loading = true
	_randomize.button_pressed = bool(cfg.get_value("launch", "randomize_order", true))
	_can_withdraw.button_pressed = bool(cfg.get_value("launch", "can_withdraw", true))
	_can_rejoin.button_pressed = bool(cfg.get_value("launch", "can_rejoin", false))
	_max_pref = maxi(1, int(cfg.get_value("launch", "max_alliance_size", 3)))
	_combat_first.button_pressed = bool(cfg.get_value("launch", "combat_first", false))
	_noncombat_first.button_pressed = bool(cfg.get_value("launch", "noncombat_first", true))
	_max_size = _max_pref
	if str(cfg.get_value("launch", "scenario", "fixed")) == "new":
		_scenario.select(1)
		_scenario_changed()
		_loading = true
	if cfg.has_section("new_scenario"):
		for key in cfg.get_section_keys("new_scenario"):
			_option_values[key] = cfg.get_value("new_scenario", key)
	for i in SEATS:
		var row: Dictionary = _rows[i]
		for key in ["mode", "faction", "alliance", "strategy", "behavior"]:
			var o: OptionButton = row[key]
			var idx := int(cfg.get_value("seat%d" % i, key, o.selected))
			if idx >= 0 and idx < o.item_count:
				o.select(idx)
		for k in SEAT_KEYS:
			if cfg.has_section_key("seat%d" % i, k[0]):
				_seat_values["%d/%s" % [i, k[0]]] = cfg.get_value("seat%d" % i, k[0])
	for key in NEUTRAL_KEYS:
		if cfg.has_section_key("neutral", key):
			_seat_values["neutral/%s" % key] = cfg.get_value("neutral", key)
	_loading = false
