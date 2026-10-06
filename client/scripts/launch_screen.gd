class_name LaunchScreen
extends Control
## The game launch screen (reference/GC Startup Screen.odt). The scenario list at the top offers
## Global Cataclysm: 1972 as it stands, a New scenario dealt afresh by the server's generator
## (engine/scenario_generator.py), and every saved scenario setup (server/setups.py): the
## settings on this screen saved under a name -- not the map they deal, which is new every game.
## Picking a saved one fills the screen with its settings to play or edit; saving it again under
## the same name updates it, under a new name makes a new one. The fixed scenario can't be saved.
##
## Below it: the scenario's name, id (assigned by the server) and description; six seats, each a
## Human, Bot, Neutral or Noncombatant (a new scenario: Human, Bot or Not playing), with for
## players a faction (or random) and a starting alliance, and for bots an alliance strategy and
## behavior (bots always play the heuristic AI); a new scenario also sets, per player seat, its
## territory value, initial MPC, units MPC, promotions and Strategic Centers, with a Neutral row
## for the Neutral pool. Then the sections Alliance, Rules, Strategic Centers and Starting
## Locations and Units (the last two, and the surrender rules, for a new scenario only).
##
## Save, Delete, Resume and Start are long presses (HoldButton). "Start Game" sends the settings
## to the server (server/lobby.py builds the game and re-checks everything); "Resume" goes back to
## a game already running there.
##
## The seat table: Seat, Type and Faction stay put; the other columns scroll sideways when
## the window is too narrow for them.
##
## The rules mirrored here for instant feedback: at least two players, at most one
## human, each faction picked once, a starting alliance needs two or more members and
## can't include every player, and a seat's initial MPC is at least its units MPC.
##
## MULTI-PLAYER (open_multi; server/hub.py): New Game, or an admin's Scenarios mode. The list is the
## server's for this player -- GC72, New scenario, the shared scenarios, then the player's own -- each
## filled with the player's saved settings when they have some. Any number of Human seats. Save: the
## player's own settings for GC72, New scenario or a shared one (unless it is renamed: then it saves a
## new scenario of their own); an own scenario saves itself, or a new one under a new name. Reset
## Settings goes back to the scenario's own; Delete is for the player's own scenarios. Start Game
## creates the game. In Scenarios mode, Save and Delete change the shared scenarios for everyone and
## there is no Start. Every action is a message for the server (`server_request`).

signal start_requested(settings: Dictionary)
signal resume_requested
signal save_requested(setup: Dictionary)   # {id, name, description, settings}: the server replies "setups"
signal delete_requested(setup_id: String)
signal server_request(msg: Dictionary)   # multi-player: a message for the server (create_game, save_settings, ...)
signal back_requested                    # multi-player: back to the main menu

var SEATS := 6  # one seat per faction: set from GameData's faction set in _ready
const MODES := [["Human", "HUMAN"], ["Bot", "BOT"], ["Neutral", "NEUTRAL"], ["Noncombatant", "NONCOMBATANT"]]
const NEW_MODES := [["Human", "HUMAN"], ["Bot", "BOT"], ["Not playing", "NOT_PLAYING"]]  # a new scenario's seats
const FIXED := "fixed"  # the scenario list's entries: FIXED, NEW, or a saved setup's id
const NEW := "new"
const ALLIANCES := ["None", "Alliance 1", "Alliance 2", "Alliance 3"]
const STRATEGIES := ["random", "aggressive", "passive", "counterweight", "independent", "adversarial", "underdog", "variable"]
# Which AI a bot seat plays (server/lobby.py's BOT_AIS): the heuristic bot (default), the random baseline, and
# Claude itself (needs ANTHROPIC_API_KEY on the server). Shown to admins only (Account.sees_bot_details).
const BOT_AIS := [["Strategy", "strategy"], ["Random", "random"], ["Claude", "claude"]]
const BEHAVIORS := ["random", "loyal", "opportunistic", "treacherous", "underdog", "variable"]
# A new scenario's seat settings: [key, header, width]; the Neutral row has no initial MPC.
const SEAT_KEYS := [["territory_value", "Territory", 84], ["initial_mpc", "Initial MPC", 84], ["units_mpc", "Units MPC", 84],
	["promotions", "Promotions", 84], ["scs", "SCs", 70]]
const NEUTRAL_KEYS := ["territory_value", "units_mpc", "promotions", "scs"]
# Which section each of the generator's scenario-wide options sits in (its label comes from the server).
const OPTION_SECTIONS := {
	"min_scs_to_avoid_surrender": "rules", "surrender_income_multiplier": "rules",
	"sc_bonus": "sc", "sc_min_distance": "sc", "sc_final_min_distance": "sc",
	"faction_weight": "starting", "infantry_in_every_territory": "starting",
	"neutral_infantry_in_every_territory": "starting", "extra_non_sc_units": "starting",
}
const HOLD_SECONDS := 1.0
const DEFAULT_ARMISTICE_ROUNDS := 10  # (server/lobby.py's DEFAULT_ARMISTICE_ROUNDS / MAX_ARMISTICE_ROUNDS)
const MAX_ARMISTICE_ROUNDS := 100
const MAX_TURN_HOURS := 720  # (server/lobby.py's MAX_TURN_HOURS)
const ROW_H := 30.0
const HEAD_H := 18.0
const LABEL_W := 205.0  # a section's labels
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
var _info: GridContainer  # the scenario's name, id and description (a new scenario only)
var _name: LineEdit
var _id_label: Label
var _description: TextEdit
var _sections := {}  # "alliance" | "rules" | "sc" | "starting" -> its GridContainer
var _sc_section: Control  # the Strategic Centers section (a new scenario only)
var _starting_section: Control  # ...and Starting Locations and Units
var _new_only_rows: Array = []  # the Rules section's rows for a new scenario only
var _territory_note: Label
var _option_specs: Array = []  # from the server: [{key, label, kind, min, max, default, help}]
var _option_controls := {}  # key -> SpinBox | CheckBox
var _option_values := {}  # key -> the value chosen (kept across the server's refreshes)
var _seat_values := {}  # "<seat>/<key>" or "neutral/<key>" -> a seat setting to show (else the default)
var _randomize: CheckBox
var _can_withdraw: CheckBox
var _can_rejoin: CheckBox
var _max_alliance: OptionButton
var _combat_first: CheckBox
var _noncombat_first: CheckBox
var _armistice_rounds: SpinBox
var _turn_hours: SpinBox
var _max_pref := 3  # the maximum alliance size the player asked for
var _max_size := 3  # ...as offered: kept within 2 .. players-1 for the players there are
var _message: Label
var _start: HoldButton
var _resume: HoldButton
var _save_button: HoldButton
var _delete_button: HoldButton
var _setups: Array = []  # the saved setups, from the server: [{id, name, description, settings}]
var _setup_id := ""  # the saved setup on the screen ("" = none: the fixed or a new scenario)
var _setup_name := ""  # ...and the name it was saved under
var _entry := FIXED  # the scenario list's current entry
var _pending_entry := ""  # remembered from the last launch: a saved setup to pick once the list arrives
var _game_running := false
var _loading := false
var remember := true  # keep the last setup in user://launch.cfg (off for scripted runs and tests)
var multi := false        # a multi-player server's New Game (or Scenarios) -- see above
var admin_mode := false   # ...its Scenarios mode: the shared scenarios are edited, nothing is started
var _entries := {}        # multi-player: id -> the server's listing entry {id, kind, name, description, settings, defaults, personal}
var _defaults := {}       # FIXED / NEW -> the screen's own default settings
var _reset_button: HoldButton
var _back_button: Button
var _title_label: Label
var _ai_col: VBoxContainer  # the Bot type column (admins only)


func _ready() -> void:
	SEATS = GameData.faction_order.size()
	z_index = 500
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var bg := ColorRect.new()
	bg.color = GCTheme.NAVY_DARK
	add_child(bg)
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	# the poster behind the sheet (a title screen: the one place texture belongs)
	var art := TextureRect.new()
	art.texture = load("res://assets/logo/splash.png")
	art.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	art.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	art.modulate = Color(0.55, 0.55, 0.55)
	art.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(art)
	art.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := PanelContainer.new()
	HudStyle.paper_sheet(panel)
	center.add_child(panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	panel.add_child(v)

	# the wordmark: GLOBAL CATACLYSM 1972, CATACLYSM in red
	var title := HBoxContainer.new()
	title.alignment = BoxContainer.ALIGNMENT_CENTER
	title.add_theme_constant_override("separation", 12)
	for word in ["Global", "Cataclysm", "1972"]:
		var w := HudStyle.label(word, 34, HudStyle.GOLD)
		if word == "Cataclysm":
			w.add_theme_color_override("font_color", GCTheme.RED)
		title.add_child(w)
	v.add_child(title)
	var rule := HSeparator.new()
	rule.theme_type_variation = "RedRule"
	v.add_child(rule)
	_title_label = HudStyle.label("", 16, GCTheme.RED)
	_title_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_title_label.visible = false
	v.add_child(_title_label)
	var scenario_row := HBoxContainer.new()
	scenario_row.alignment = BoxContainer.ALIGNMENT_CENTER
	scenario_row.add_theme_constant_override("separation", 10)
	scenario_row.add_child(HudStyle.label("Scenario:", 15, HudStyle.TEXT_DIM))
	_scenario = OptionButton.new()
	_scenario.focus_mode = Control.FOCUS_NONE
	_scenario.custom_minimum_size = Vector2(320, ROW_H)
	_scenario.item_selected.connect(func(i: int): _entry_picked(str(_scenario.get_item_metadata(i))))
	scenario_row.add_child(_scenario)
	v.add_child(scenario_row)
	_rebuild_scenario_list()

	_build_info(v)
	v.add_child(HSeparator.new())
	_build_seat_table(v)
	_territory_note = HudStyle.label("", 12, HudStyle.TEXT_DIM)
	_territory_note.visible = false
	v.add_child(_territory_note)
	v.add_child(HSeparator.new())
	_build_sections(v)

	_message = HudStyle.label("", 12, GCTheme.RED)
	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_message.custom_minimum_size = Vector2(760, 34)
	v.add_child(_message)

	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 14)
	v.add_child(buttons)
	_delete_button = _button("Delete Scenario Setup")
	_delete_button.activated.connect(func():
		if _setup_id == "":
			return
		if multi:
			server_request.emit({"type": "delete_shared" if admin_mode else "delete_scenario", "id": _setup_id})
		else:
			delete_requested.emit(_setup_id))
	buttons.add_child(_delete_button)
	_save_button = _button("Save Scenario Setup")
	_save_button.activated.connect(func():
		if multi:
			_multi_save()
		else:
			save_requested.emit(setup()))
	buttons.add_child(_save_button)
	_reset_button = _button("Reset Settings")
	_reset_button.visible = false
	_reset_button.activated.connect(_multi_reset)
	buttons.add_child(_reset_button)
	var gap := Control.new()
	gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gap.custom_minimum_size = Vector2(20, 0)
	buttons.add_child(gap)
	_resume = _button("Resume current game")
	_resume.activated.connect(func(): resume_requested.emit())
	buttons.add_child(_resume)
	_start = HudStyle.primary(_button("Start Game"))
	_start.activated.connect(func():
		if multi:
			server_request.emit({"type": "create_game", "scenario": {"kind": _entry_kind(), "id": _entry}, "settings": settings()})
			return
		_save()
		start_requested.emit(settings()))
	buttons.add_child(_start)
	_back_button = Button.new()
	_back_button.text = "Back"
	HudStyle.secondary(_back_button)
	_back_button.custom_minimum_size = Vector2(120, 44)
	_back_button.visible = false
	_back_button.pressed.connect(func(): back_requested.emit())
	buttons.add_child(_back_button)
	var hint := HudStyle.label("Press and hold a button to use it.", 11, HudStyle.TEXT_DIM)
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	v.add_child(hint)

	get_viewport().size_changed.connect(_fit_scroll)
	_set_new_visible(false)
	_defaults[FIXED] = settings()  # (the screen as built: GC72's defaults; New scenario's follow from them)
	_defaults[NEW] = _new_defaults(_defaults[FIXED])
	_load()
	_changed()


# ---- building ------------------------------------------------------------------------------------

## The scenario's name, id and description.
func _build_info(parent: VBoxContainer) -> void:
	_info = GridContainer.new()
	_info.columns = 2
	_info.add_theme_constant_override("h_separation", 10)
	_info.add_theme_constant_override("v_separation", 6)
	parent.add_child(_info)
	_info.add_child(_field_label("Scenario Name:"))
	_name = LineEdit.new()
	_name.custom_minimum_size = Vector2(420, ROW_H)
	_name.max_length = 60
	_name.placeholder_text = "Name this setup to save it"
	_name.text_changed.connect(func(_t): _changed())
	_info.add_child(_name)
	_info.add_child(_field_label("Scenario ID:"))
	_id_label = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	_id_label.custom_minimum_size = Vector2(0, 22)
	_info.add_child(_id_label)
	var d := _field_label("Description:")
	d.vertical_alignment = VERTICAL_ALIGNMENT_TOP
	_info.add_child(d)
	_description = TextEdit.new()
	_description.custom_minimum_size = Vector2(720, 64)
	_description.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_description.placeholder_text = "What this setup is for"
	_info.add_child(_description)


func _field_label(text: String) -> Label:
	var l := HudStyle.label(text, 14, HudStyle.TEXT_DIM)
	l.custom_minimum_size = Vector2(120, ROW_H)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	return l


## The four sections side by side (wrapping when the window is narrow). The generator's options
## are added to Rules, Strategic Centers and Starting Locations and Units when the server
## describes them (set_new_scenario_options).
func _build_sections(parent: VBoxContainer) -> void:
	var flow := HFlowContainer.new()
	flow.add_theme_constant_override("h_separation", 26)
	flow.add_theme_constant_override("v_separation", 10)
	parent.add_child(flow)
	for s in [["alliance", "Alliance"], ["rules", "Rules"], ["sc", "Strategic Centers"], ["starting", "Starting Locations and Units"]]:
		var box := VBoxContainer.new()
		box.add_theme_constant_override("separation", 4)
		flow.add_child(box)
		box.add_child(HudStyle.label(s[1], 15, HudStyle.GOLD))
		var grid := GridContainer.new()
		grid.columns = 2
		grid.add_theme_constant_override("h_separation", 10)
		grid.add_theme_constant_override("v_separation", 2)
		box.add_child(grid)
		_sections[s[0]] = grid
		if s[0] == "sc":
			_sc_section = box
		elif s[0] == "starting":
			_starting_section = box

	_max_alliance = _option([], 70)
	_max_alliance.item_selected.connect(func(i: int):
		_max_pref = int(_max_alliance.get_item_text(i))
		_max_size = _max_pref
		_changed())
	_max_alliance.tooltip_text = "The most factions one alliance may hold, from 1 to the number of players minus one. 1 means no alliances at all, and no Alliances phase."
	_section_row("alliance", "Maximum size:", _max_alliance)
	_can_withdraw = _checkbox(true)
	_section_row("alliance", "Can withdraw:", _can_withdraw, "Players can withdraw from alliances.")
	_can_rejoin = _checkbox(false)
	_section_row("alliance", "Can rejoin:", _can_rejoin, "Players can rejoin alliances they left.")
	_randomize = _checkbox(true)
	_section_row("rules", "Randomize turn order:", _randomize)
	_noncombat_first = _checkbox(true)
	_section_row("rules", "First turn non-combat moves:", _noncombat_first, "Non-Combat Moves allowed on a faction's first turn.")
	_combat_first = _checkbox(false)
	_section_row("rules", "First turn combat moves:", _combat_first, "Combat Moves allowed on a faction's first turn.")
	_armistice_rounds = SpinBox.new()
	_armistice_rounds.min_value = 0
	_armistice_rounds.max_value = MAX_ARMISTICE_ROUNDS
	_armistice_rounds.step = 1
	_armistice_rounds.value = DEFAULT_ARMISTICE_ROUNDS
	_armistice_rounds.update_on_text_changed = true
	_armistice_rounds.custom_minimum_size = Vector2(90, 26)
	_section_row("rules", "Rounds until armistice proposal:", _armistice_rounds,
		"After this many rounds the game proposes an armistice: bots always accept, and human players can choose to continue. If it is declined, it is proposed again every 5 rounds. 0: never.")
	_turn_hours = SpinBox.new()
	_turn_hours.min_value = 0
	_turn_hours.max_value = MAX_TURN_HOURS
	_turn_hours.step = 1
	_turn_hours.value = 0
	_turn_hours.update_on_text_changed = true
	_turn_hours.custom_minimum_size = Vector2(90, 26)
	_section_row("rules", "Hours to take turn:", _turn_hours,
		"How long a player has for a whole turn. Once it runs over, the host can have a bot take their seat (or anyone can, when the host is the slow one). 0: no limit.")


## A label and its control, in `section`. Returns both (to show or hide together).
func _section_row(section: String, text: String, control: Control, help := "") -> Array:
	var l := HudStyle.label(text, 13)
	l.custom_minimum_size = Vector2(LABEL_W, 26)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	if help != "":
		l.tooltip_text = help
		l.mouse_filter = Control.MOUSE_FILTER_PASS
		control.tooltip_text = help
	_sections[section].add_child(l)
	_sections[section].add_child(control)
	return [l, control]


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
	_ai_col = _column(moving, "Bot type", 120)

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
		row["ai"] = _option(BOT_AIS.map(func(a): return a[0]), 120)
		row["ai"].tooltip_text = "Strategy: the heuristic bot (styles, objectives, risk checks).\nRandom: the baseline bot, for comparison.\nClaude: Claude plays it (the server needs ANTHROPIC_API_KEY)."
		_ai_col.add_child(row["ai"])
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


func _checkbox(on: bool) -> CheckBox:
	var c := CheckBox.new()
	c.button_pressed = on
	c.focus_mode = Control.FOCUS_NONE
	HudStyle.style_checkbox(c)
	c.toggled.connect(func(_on): _changed())
	return c


func _button(text: String) -> HoldButton:
	var b := HoldButton.new()
	b.button_down.connect(_commit_typing)  # (the buttons take no focus, so a box being typed in keeps it)
	b.hold_seconds = HOLD_SECONDS
	b.space_key = false  # four of them on show: Space would be ambiguous
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(220, 44)
	HudStyle.secondary(b)
	return b


## A number still being typed into a SpinBox counts before a button acts on the settings.
func _commit_typing() -> void:
	var focused := get_viewport().gui_get_focus_owner()
	if focused is LineEdit and focused.get_parent() is SpinBox:
		var text := (focused as LineEdit).text.strip_edges()
		if text.is_valid_float():
			(focused.get_parent() as SpinBox).value = float(text)
		focused.release_focus()


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
	return _entry != FIXED


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
			"ai": BOT_AIS[(row["ai"] as OptionButton).selected][1],
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
	s["armistice_rounds"] = int(_armistice_rounds.value)
	s["turn_hours"] = int(_turn_hours.value)
	if Dbg.args.has("combat_first_turn"):
		s["dev"] = {"combat_first_turn": true}  # scripted runs only: the rules skip it
	return s


## What Save sends: {id, name, description, settings}. The server keeps `id` while the name is
## the one it was saved under, and makes a new setup otherwise.
func setup() -> Dictionary:
	var s := settings()
	s.erase("dev")
	return {"id": _setup_id if _setup_id != "" else null, "name": _name.text.strip_edges(),
		"description": _description.text.strip_edges(), "settings": s}


## True when Save makes a new setup (nothing saved is on the screen, or its name was changed).
func saves_as_new() -> bool:
	return _setup_id == "" or _name.text.strip_edges() != _setup_name


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
	if humans > 1 and not multi:
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
		mode_o.set_item_disabled(0, not multi and human_seat >= 0 and human_seat != i)  # at most one human (one-game server)
		var fac_o: OptionButton = row["faction"]
		for k in range(1, fac_o.item_count):
			var code := str(fac_o.get_item_metadata(k))
			fac_o.set_item_disabled(k, taken.has(code) and taken[code] != i)
		var f := _faction_of(row)
		(row["chip"] as ColorRect).color = GameData.factions[f].color if f != "random" else GCTheme.GRAY
		var player: bool = mode == "HUMAN" or mode == "BOT"
		(row["alliance"] as OptionButton).disabled = not player or _max_size < 2
		if _max_size < 2:
			(row["alliance"] as OptionButton).select(0)
		(row["strategy"] as OptionButton).disabled = mode != "BOT"
		(row["behavior"] as OptionButton).disabled = mode != "BOT"
		(row["ai"] as OptionButton).disabled = mode != "BOT"
		for k in SEAT_KEYS:
			(row[k[0]] as SpinBox).editable = player
			(row[k[0]] as SpinBox).modulate.a = 1.0 if player else 0.35
	_can_rejoin.disabled = not _can_withdraw.button_pressed  # nobody leaves, so nobody rejoins
	_refresh_territory()
	var p := problems()
	_message.text = "\n".join(p) if not p.is_empty() else ""
	_start.disabled = not p.is_empty()
	_resume.visible = _game_running and not multi
	_ai_col.visible = Account.sees_bot_details()
	_start.visible = not admin_mode
	_back_button.visible = multi
	if multi:
		_refresh_multi_buttons(p)
	else:
		_refresh_save_buttons(p)
	_fit_scroll.call_deferred()


## Save needs a new scenario with a name and a setup that could start; its label says whether it
## updates the saved setup or makes a new one. Delete is for a saved setup only.
func _refresh_save_buttons(p: Array) -> void:
	_save_button.visible = _is_new()
	_delete_button.visible = _is_new()
	_delete_button.disabled = _setup_id == ""
	_save_button.disabled = _name.text.strip_edges() == "" or not p.is_empty()
	_save_button.text = ("Save as New Scenario Setup" if _setup_id != "" and saves_as_new() else "Save Scenario Setup").to_upper()
	if _setup_id == "":
		_id_label.text = "assigned when saved"
	elif saves_as_new():
		_id_label.text = "%s  (a new name saves a new scenario)" % _setup_id
	else:
		_id_label.text = _setup_id


## Show the screen (again). `game_running`: the server has a game to go back to.
## `new_scenario`: the server's New Scenario description ({"options": [...], "seats": {...}}).
## `setups`: the saved scenario setups.
func open(game_running: bool, new_scenario := {}, setups := []) -> void:
	_game_running = game_running
	set_new_scenario_options(new_scenario.get("options", []))
	set_seat_specs(new_scenario.get("seats", {}))
	set_setups(setups)
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


# ---- the scenario list ------------------------------------------------------------------

## The scenario list: the fixed scenario, a new one, then the saved setups by name (each with its
## description as a tooltip); the current entry stays selected.
func _rebuild_scenario_list() -> void:
	_scenario.clear()
	_scenario.add_item("Global Cataclysm: 1972")
	_scenario.set_item_metadata(0, FIXED)
	_scenario.add_item("New scenario")
	_scenario.set_item_metadata(1, NEW)
	if multi:
		for group in [["shared", "Shared scenarios"], ["own", "My scenarios"]]:
			var rows := _setups.filter(func(r): return str(r.get("kind", "")) == group[0])
			if rows.is_empty():
				continue
			_scenario.add_separator(group[1])
			for s in rows:
				_scenario.add_item(str(s["name"]))
				var i := _scenario.item_count - 1
				_scenario.set_item_metadata(i, str(s["id"]))
				_scenario.get_popup().set_item_tooltip(i, str(s.get("description", "")))
	elif not _setups.is_empty():
		_scenario.add_separator("Saved scenarios")
		for s in _setups:
			_scenario.add_item(str(s["name"]))
			var i := _scenario.item_count - 1
			_scenario.set_item_metadata(i, str(s["id"]))
			_scenario.get_popup().set_item_tooltip(i, str(s.get("description", "")))
	for i in _scenario.item_count:
		if not _scenario.is_item_separator(i) and str(_scenario.get_item_metadata(i)) == _entry:
			_scenario.select(i)
			return
	_scenario.select(1 if _is_new() else 0)


func _setup_by_id(setup_id: String) -> Dictionary:
	for s in _setups:
		if str(s["id"]) == setup_id:
			return s
	return {}


## The saved setups, from the server ("lobby", and "setups" after a save or delete). `selected`:
## the setup just saved (it stays on the screen as it is, now under its id), or null after a
## delete (the screen becomes a new scenario with the same settings, unnamed).
func set_setups(setups: Array, selected = "keep") -> void:
	_setups = setups
	if selected == null:
		_entry = NEW
		_setup_id = ""
		_setup_name = ""
		_name.text = ""
		_description.text = ""
	elif str(selected) != "keep":
		var s := _setup_by_id(str(selected))
		_entry = str(selected)
		_setup_id = str(selected)
		_setup_name = str(s.get("name", _name.text.strip_edges()))
		_name.text = _setup_name
	elif _setup_id != "" and _setup_by_id(_setup_id).is_empty():
		_entry = NEW  # deleted elsewhere: what is on the screen becomes an unsaved new scenario
		_setup_id = ""
		_setup_name = ""
	_rebuild_scenario_list()
	if _pending_entry != "":
		var pending := _pending_entry
		_pending_entry = ""
		if not _setup_by_id(pending).is_empty():
			_entry_picked(pending)
	_changed()


## A pick from the scenario list: a saved setup fills the screen with its settings; the fixed or a
## new scenario keeps the settings on the screen (the seat types switching over).
func _entry_picked(entry: String) -> void:
	var was_new := _is_new()
	_entry = entry
	if entry != FIXED and entry != NEW:
		var s := _setup_by_id(entry)
		if s.is_empty():
			_entry = NEW
		else:
			_setup_id = entry
			_setup_name = str(s["name"])
			_name.text = _setup_name
			_description.text = str(s.get("description", ""))
			apply_settings(s["settings"])
			_rebuild_scenario_list()
			return
	_setup_id = ""
	_setup_name = ""
	_name.text = ""
	_description.text = ""
	if multi:  # GC72 / New scenario: the player's saved settings for it, else its defaults
		var saved = _entries.get(entry, {}).get("settings")
		apply_settings(saved if saved is Dictionary else _defaults[entry])
		_entry = entry
		_rebuild_scenario_list()
		_changed()
		return
	_rebuild_scenario_list()
	if was_new != _is_new():
		_scenario_changed()
	else:
		_changed()


# ---- multi-player: New Game and Scenarios ----------------------------------------------------------

## Opens as a multi-player server's New Game (or, `admin`, its Scenarios mode) from its "scenarios" reply.
func open_multi(msg: Dictionary, admin: bool) -> void:
	multi = true
	admin_mode = admin
	remember = false  # (the server keeps a player's settings)
	var spec: Dictionary = msg.get("new_scenario", {})
	set_new_scenario_options(spec.get("options", []))
	set_seat_specs(spec.get("seats", {}))
	_title_label.text = "SCENARIOS  -  EDITING THE SHARED SCENARIOS" if admin else ""
	_title_label.visible = admin
	set_scenarios(msg.get("scenarios", []), msg.get("selected"))
	_message.text = ""
	visible = true


## The server's listing for this player ("scenarios", after every save, reset or delete). `selected`:
## the scenario just saved (it is shown as saved), or null (keep what is on the screen, if it still exists).
func set_scenarios(rows: Array, selected = null) -> void:
	_entries = {}
	var listed := []
	for r in rows:
		if admin_mode:  # (an admin edits the scenarios themselves, not their own settings for them)
			r = r.duplicate()
			r["settings"] = r.get("defaults")
			r["personal"] = false
		_entries[str(r["id"])] = r
		var kind := str(r.get("kind", ""))
		if kind == "shared" or (kind == "own" and not admin_mode):
			listed.append(r)
	var current := _entry
	_setups = listed
	if selected != null and _entries.has(str(selected)):
		_entry = "x"  # (force a fresh pick of the saved one)
		_entry_picked(str(selected))
	elif not _entries.has(current) and current != FIXED and current != NEW:
		_entry_picked(NEW)
	else:
		_entry = "x"
		_entry_picked(current)


## The kind of the scenario on the screen, as the server names them: fixed, new, shared or own.
func _entry_kind() -> String:
	if _entry == FIXED or _entry == NEW:
		return _entry
	return str(_entries.get(_entry, {}).get("kind", "new"))


## The scenario on the screen saved as it would be now: renamed, it becomes a new scenario.
func _renamed() -> bool:
	return _is_new() and _name.text.strip_edges() != "" and _name.text.strip_edges() != _setup_name


func _multi_save() -> void:
	var kind := _entry_kind()
	var s := settings()
	s.erase("dev")
	var doc := {"id": _setup_id if _setup_id != "" else null, "name": _name.text.strip_edges(),
		"description": _description.text.strip_edges(), "settings": s}
	if admin_mode and _entry == FIXED:  # GC72's default settings, for everyone
		server_request.emit({"type": "save_shared", "scenario": {"id": "fixed", "settings": s}})
	elif admin_mode:
		server_request.emit({"type": "save_shared", "scenario": doc})
	elif kind == "own" or _renamed():
		if kind != "own":
			doc["id"] = null
		server_request.emit({"type": "save_scenario", "scenario": doc})
	else:
		server_request.emit({"type": "save_settings", "scenario_id": _entry, "settings": s})


func _multi_reset() -> void:
	if _entry_kind() == "own":
		_entry_picked(_entry)  # its last saved version
		return
	server_request.emit({"type": "reset_settings", "scenario_id": _entry})


func _refresh_multi_buttons(p: Array) -> void:
	var kind := _entry_kind()
	var named := _name.text.strip_edges() != ""
	_reset_button.visible = not admin_mode
	_reset_button.disabled = kind != "own" and not bool(_entries.get(_entry, {}).get("personal", false))
	_delete_button.visible = (kind == "own" and not admin_mode) or (kind == "shared" and admin_mode)
	_delete_button.disabled = _setup_id == ""
	_delete_button.text = "DELETE SHARED SCENARIO" if admin_mode else "DELETE MY SCENARIO"
	_save_button.visible = not admin_mode or _is_new() or _entry == FIXED
	_save_button.disabled = not p.is_empty() or (admin_mode and not named and _entry != FIXED) or (kind == "own" and not named)
	var label := "Save My Settings"
	if admin_mode and _entry == FIXED:
		label = "Save GC72 Settings"
	elif admin_mode:
		label = "Save Shared Scenario" if kind == "shared" and not _renamed() else "Save as New Shared Scenario"
	elif kind == "own":
		label = "Save as New Scenario" if _renamed() else "Save Scenario"
	elif _renamed():
		label = "Save as New Scenario"
	_save_button.text = label.to_upper()
	if kind == "own":
		_id_label.text = "%s  (yours%s)" % [_setup_id, "; a new name saves a new scenario" if _renamed() else ""]
	elif kind == "shared":
		_id_label.text = "%s  (shared%s)" % [_setup_id, "; a new name saves a new scenario" if _renamed() else ""]
	else:
		_id_label.text = "name it to save it as a scenario of your own"


static func _new_defaults(fixed: Dictionary) -> Dictionary:
	var s := fixed.duplicate(true)
	for seat in s["seats"]:
		if not ["HUMAN", "BOT"].has(str(seat["mode"])):
			seat["mode"] = "NOT_PLAYING"
	s["scenario"] = {"kind": "new", "options": {}, "neutral": {}}
	return s


## "fixed" or "new" (scripted runs).
func select_scenario(kind: String) -> void:
	_entry_picked(NEW if kind == "new" else FIXED)


## Switching between the fixed scenario and a new one: the seat types change (a Neutral or
## Noncombatant seat becomes Not playing, and back), and the new scenario's settings show.
func _scenario_changed() -> void:
	var was_loading := _loading
	_loading = true
	for row in _rows:
		var o: OptionButton = row["mode"]
		var old: int = o.selected
		_fill_modes(o)
		if _is_new():
			o.select(mini(old, 2))  # Human, Bot, the rest Not playing
		else:
			o.select(old if old < 2 else 3)  # Not playing -> Noncombatant
	_loading = was_loading
	_set_new_visible(_is_new())
	_changed()


func _fill_modes(o: OptionButton) -> void:
	o.clear()
	for m in _modes():
		o.add_item(m[0])


func _set_new_visible(on: bool) -> void:
	for col in _new_columns:
		col.visible = on
	for cell in _neutral_cells:
		cell.visible = on
	for c in [_info, _sc_section, _starting_section, _territory_note]:
		if c != null:
			c.visible = on
	for c in _new_only_rows:
		c.visible = on


## Fills the screen from `s` (launch settings, as settings() makes them): a saved setup's, or the
## last launch's. What `s` leaves out takes the defaults.
func apply_settings(s: Dictionary) -> void:
	_loading = true
	var sc: Dictionary = s.get("scenario", {}) if s.get("scenario") is Dictionary else {}
	var new_kind: bool = str(sc.get("kind", FIXED)) == "new"
	if new_kind != _is_new():
		_entry = NEW if new_kind else FIXED
	var modes := _modes()
	var seats: Array = s.get("seats", []) if s.get("seats") is Array else []
	for i in SEATS:
		var row: Dictionary = _rows[i]
		var seat: Dictionary = seats[i] if i < seats.size() and seats[i] is Dictionary else {}
		var mode_o: OptionButton = row["mode"]
		_fill_modes(mode_o)
		var m := modes.size() - 1
		for j in modes.size():
			if modes[j][1] == str(seat.get("mode", "")):
				m = j
		mode_o.select(m)
		var fac_o: OptionButton = row["faction"]
		fac_o.select(0)
		for j in range(1, fac_o.item_count):
			if str(fac_o.get_item_metadata(j)) == str(seat.get("faction", "random")):
				fac_o.select(j)
		(row["alliance"] as OptionButton).select(clampi(int(seat.get("alliance", 0)), 0, ALLIANCES.size() - 1))
		(row["strategy"] as OptionButton).select(maxi(0, STRATEGIES.find(str(seat.get("strategy", "random")))))
		(row["behavior"] as OptionButton).select(maxi(0, BEHAVIORS.find(str(seat.get("behavior", "random")))))
		var ai_index := 0
		for j in BOT_AIS.size():
			if BOT_AIS[j][1] == str(seat.get("ai", "strategy")):
				ai_index = j
		(row["ai"] as OptionButton).select(ai_index)
		for k in SEAT_KEYS:
			var key := "%d/%s" % [i, k[0]]
			if seat.has(k[0]):
				_seat_values[key] = seat[k[0]]
			else:
				_seat_values.erase(key)
		_territory_auto[i] = not seat.has("territory_value")
	var neutral: Dictionary = sc.get("neutral", {}) if sc.get("neutral") is Dictionary else {}
	for k in NEUTRAL_KEYS:
		if neutral.has(k):
			_seat_values["neutral/%s" % k] = neutral[k]
		else:
			_seat_values.erase("neutral/%s" % k)
	_territory_auto["neutral"] = not neutral.has("territory_value")
	_option_values = (sc.get("options", {}) as Dictionary).duplicate() if sc.get("options") is Dictionary else {}
	_randomize.button_pressed = bool(s.get("randomize_order", true))
	_can_withdraw.button_pressed = bool(s.get("can_withdraw", true))
	_can_rejoin.button_pressed = bool(s.get("can_rejoin", false))
	_combat_first.button_pressed = bool(s.get("allow_combat_first_turn", false))
	_noncombat_first.button_pressed = bool(s.get("allow_noncombat_first_turn", true))
	_armistice_rounds.set_value_no_signal(float(s.get("armistice_rounds", DEFAULT_ARMISTICE_ROUNDS)))
	_turn_hours.set_value_no_signal(float(s.get("turn_hours", 0)))
	_max_pref = maxi(1, int(s.get("max_alliance_size", 3)))
	_max_size = _max_pref
	_loading = false
	_apply_seat_values()
	_apply_option_values()
	_set_new_visible(_is_new())
	_changed()


# ---- a new scenario's settings ----------------------------------------------------------

## The seat settings' ranges and defaults, from the server.
func set_seat_specs(specs: Dictionary) -> void:
	if specs.is_empty() or _seat_specs == specs:
		return
	_seat_specs = specs
	_apply_seat_values()


## Each seat setting as chosen (_seat_values), else the server's default.
func _apply_seat_values() -> void:
	if _seat_specs.is_empty():
		return
	_updating = true
	for spec in _seat_specs.get("players", []):
		var key := str(spec["key"])
		for i in SEATS:
			_set_spin(_rows[i][key], spec, _seat_values.get("%d/%s" % [i, key]))
	for spec in _seat_specs.get("neutral", []):
		var key := str(spec["key"])
		_set_spin(_neutral_row[key], spec, _seat_values.get("neutral/%s" % key))
	_updating = false
	_refresh_territory()


func _set_spin(sb: SpinBox, spec: Dictionary, value) -> void:
	sb.min_value = float(spec["min"])
	sb.max_value = float(spec["max"])
	if value != null:
		sb.value = float(value)
	elif spec["default"] != null:
		sb.value = float(spec["default"])


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
		for k in SEAT_KEYS:
			_seat_values["%d/%s" % [seat, k[0]]] = int(row[k[0]].value)
	else:
		_seat_values["neutral/%s" % key] = int(_neutral_row[key].value)
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


## The generator's scenario-wide options, as the server describes them, each in its section.
func set_new_scenario_options(specs: Array) -> void:
	if specs.is_empty() or _option_specs == specs:
		return
	_option_specs = specs
	for c in _option_controls.values():
		c.get_meta("label").queue_free()
		c.queue_free()
	_option_controls.clear()
	_new_only_rows = []
	for spec in specs:
		var key := str(spec["key"])
		var section: String = OPTION_SECTIONS.get(key, "starting")
		var control: Control
		if str(spec["kind"]) == "bool":
			var cb := CheckBox.new()
			cb.focus_mode = Control.FOCUS_NONE
			HudStyle.style_checkbox(cb)
			cb.toggled.connect(func(on: bool): _option_values[key] = on)
			control = cb
		else:
			var sb := SpinBox.new()
			sb.update_on_text_changed = true  # a typed number counts at once, not only on Enter
			sb.min_value = float(spec["min"])
			sb.max_value = float(spec["max"])
			sb.step = 1.0 if str(spec["kind"]) == "int" else 0.1
			sb.custom_minimum_size = Vector2(90, 26)
			sb.value_changed.connect(func(x: float): _option_values[key] = int(x) if str(spec["kind"]) == "int" else x)
			control = sb
		var pair := _section_row(section, "%s:" % str(spec["label"]), control, str(spec.get("help", "")))
		control.set_meta("label", pair[0])
		_option_controls[key] = control
		if section == "rules":
			_new_only_rows.append_array(pair)
	_apply_option_values()
	_set_new_visible(_is_new())


## Each option's control shows the value chosen (_option_values), else the server's default.
func _apply_option_values() -> void:
	for spec in _option_specs:
		var key := str(spec["key"])
		if not _option_controls.has(key):
			continue
		var value = _option_values.get(key, spec["default"])
		var c = _option_controls[key]
		if c is CheckBox:
			(c as CheckBox).set_pressed_no_signal(bool(value))
		else:
			(c as SpinBox).set_value_no_signal(float(value) if value != null else float(spec["max"]))
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
	for key in _option_values:  # (chosen before the server described them)
		if not out.has(key):
			out[key] = _option_values[key]
	return out


# ---- remembering the last setup ---------------------------------------------------------

## The last launch: the scenario list's entry and the settings on the screen. A saved setup is
## picked again (with its saved settings) once the server's list arrives.
func _save() -> void:
	if Dbg.args.has("shot") or not remember:
		return
	var cfg := ConfigFile.new()
	cfg.set_value("launch", "entry", _entry)
	cfg.set_value("launch", "settings", JSON.stringify(settings()))
	cfg.save(PATH)


func _load() -> void:
	if Dbg.args.has("shot") or not remember:
		return
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK or not cfg.has_section_key("launch", "settings"):
		return  # (an older launch.cfg: the defaults)
	var s = JSON.parse_string(str(cfg.get_value("launch", "settings", "")))
	if s is Dictionary:
		apply_settings(s)
	var entry := str(cfg.get_value("launch", "entry", FIXED))
	if entry != FIXED and entry != NEW:
		_pending_entry = entry
	_rebuild_scenario_list()
