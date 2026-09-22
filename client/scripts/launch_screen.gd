class_name LaunchScreen
extends Control
## The game launch screen: six seats, each a Human, Bot, Defense or Neutral, and for
## players (humans and bots) a faction (or random), a starting alliance, and for
## bots an alliance strategy and behavior; plus whether to randomise the turn order.
## "Start Game" sends the settings to the server (server/lobby.py builds the game and
## re-checks everything); "Resume" goes back to a game already running there.
##
## The rules mirrored here for instant feedback: at least two players, at most one
## human, each faction picked once, and a starting alliance needs two or more
## members and can't include every player.

signal start_requested(settings: Dictionary)
signal resume_requested

const SEATS := 6
const MODES := [["Human", "HUMAN"], ["Bot", "BOT"], ["Defense", "DEFENSIVE"], ["Neutral", "NEUTRAL"]]
const ALLIANCES := ["None", "Alliance 1", "Alliance 2", "Alliance 3"]
const STRATEGIES := ["random", "aggressive", "passive", "counterweight", "independent", "variable"]
const BEHAVIORS := ["random", "loyal", "opportunistic", "treacherous", "variable"]
const BOT_AIS := [["Strategy", "strategy"], ["Random", "random"], ["Claude", "claude"]]  # [label, server value]: the heuristic bot (default), the random baseline, and Claude itself (needs ANTHROPIC_API_KEY set on the server)
const PATH := "user://launch.cfg"

var _rows: Array = []  # per seat: {mode, faction, chip, alliance, strategy, behavior, ai}
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
	var sub := HudStyle.label("New game", 15, HudStyle.TEXT_DIM)
	sub.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(sub)
	v.add_child(HSeparator.new())

	var grid := GridContainer.new()
	grid.columns = 8
	grid.add_theme_constant_override("h_separation", 10)
	grid.add_theme_constant_override("v_separation", 6)
	v.add_child(grid)
	for h in ["Seat", "Type", "Faction", "", "Starting alliance", "Alliance strategy", "Alliance behavior", "Bot AI"]:
		grid.add_child(HudStyle.label(h, 12, HudStyle.TEXT_DIM))
	for i in SEATS:
		_add_row(grid, i)

	_randomize = CheckBox.new()
	_randomize.text = "Randomize turn order"
	_randomize.button_pressed = true
	_randomize.focus_mode = Control.FOCUS_NONE
	_check_style(_randomize)
	_randomize.toggled.connect(func(_on): _changed())
	v.add_child(_randomize)

	_can_withdraw = CheckBox.new()
	_can_withdraw.text = "Players can withdraw from alliances"
	_can_withdraw.button_pressed = true
	_can_withdraw.focus_mode = Control.FOCUS_NONE
	_check_style(_can_withdraw)
	_can_withdraw.toggled.connect(func(_on): _changed())
	v.add_child(_can_withdraw)
	_can_rejoin = CheckBox.new()
	_can_rejoin.text = "Players can rejoin alliances they left"
	_can_rejoin.button_pressed = false
	_can_rejoin.focus_mode = Control.FOCUS_NONE
	_check_style(_can_rejoin)
	_can_rejoin.toggled.connect(func(_on): _changed())
	v.add_child(_can_rejoin)
	_combat_first = CheckBox.new()
	_combat_first.text = "Combat Moves allowed on a faction's first turn"
	_combat_first.button_pressed = false
	_combat_first.focus_mode = Control.FOCUS_NONE
	_check_style(_combat_first)
	v.add_child(_combat_first)
	_noncombat_first = CheckBox.new()
	_noncombat_first.text = "Non-Combat Moves allowed on a faction's first turn"
	_noncombat_first.button_pressed = true
	_noncombat_first.focus_mode = Control.FOCUS_NONE
	_check_style(_noncombat_first)
	v.add_child(_noncombat_first)
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

	_load()
	_changed()


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
	o.custom_minimum_size = Vector2(width, 30)
	for it in items:
		o.add_item(str(it))
	o.item_selected.connect(func(_i): _changed())
	return o


func _add_row(grid: GridContainer, i: int) -> void:
	grid.add_child(HudStyle.label("%d" % (i + 1), 15, HudStyle.GOLD))
	var mode := _option(MODES.map(func(m): return m[0]), 110)
	mode.select(0 if i == 0 else 1 if i == 1 else 3)  # a Human, a Bot, the rest Neutral
	grid.add_child(mode)

	var faction := _option(["Random"], 250)
	for code in GameData.faction_order:
		faction.add_item("%s  -  %s" % [code, GameData.factions[code].name])
		faction.set_item_metadata(faction.item_count - 1, code)
	grid.add_child(faction)
	var chip := ColorRect.new()
	chip.custom_minimum_size = Vector2(14, 30)
	grid.add_child(chip)

	var alliance := _option(ALLIANCES, 120)
	grid.add_child(alliance)
	var strategy := _option(STRATEGIES.map(func(s): return str(s).capitalize()), 140)
	grid.add_child(strategy)
	var behavior := _option(BEHAVIORS.map(func(s): return str(s).capitalize()), 150)
	grid.add_child(behavior)
	var ai := _option(BOT_AIS.map(func(a): return a[0]), 120)
	ai.tooltip_text = "Strategy: the heuristic bot (styles, objectives, risk checks).\nRandom: the baseline bot, for comparison."
	grid.add_child(ai)
	_rows.append({"mode": mode, "faction": faction, "chip": chip, "alliance": alliance, "strategy": strategy, "behavior": behavior, "ai": ai})


# ---- reading the screen -----------------------------------------------------------

func _mode_of(row: Dictionary) -> String:
	return MODES[(row["mode"] as OptionButton).selected][1]


func _faction_of(row: Dictionary) -> String:
	var o: OptionButton = row["faction"]
	return "random" if o.selected == 0 else str(o.get_item_metadata(o.selected))


func settings() -> Dictionary:
	var seats := []
	for row in _rows:
		var mode := _mode_of(row)
		var player: bool = mode == "HUMAN" or mode == "BOT"
		seats.append({
			"mode": mode,
			"faction": _faction_of(row),
			"alliance": (row["alliance"] as OptionButton).selected if player else 0,
			"strategy": STRATEGIES[(row["strategy"] as OptionButton).selected],
			"behavior": BEHAVIORS[(row["behavior"] as OptionButton).selected],
			"ai": BOT_AIS[(row["ai"] as OptionButton).selected][1],
		})
	var s := {"seats": seats, "randomize_order": _randomize.button_pressed,
		"can_withdraw": _can_withdraw.button_pressed, "can_rejoin": _can_rejoin.button_pressed}
	s["max_alliance_size"] = _max_size
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
		(row["ai"] as OptionButton).disabled = mode != "BOT"
	_can_rejoin.disabled = not _can_withdraw.button_pressed  # nobody leaves, so nobody rejoins
	var p := problems()
	_message.text = "\n".join(p) if not p.is_empty() else ""
	_start.disabled = not p.is_empty()
	_resume.visible = _game_running


## Show the screen (again). `game_running`: the server has a game to go back to.
func open(game_running: bool) -> void:
	_game_running = game_running
	_changed()
	visible = true


func close() -> void:
	visible = false


## A server rejection of the settings: show its reasons.
func show_error(message: String) -> void:
	_message.text = message


## The size options are 2 .. players-1; the choice is kept (clamped) as players come and go.
func _refresh_max_alliance() -> void:
	var players := 0
	for row in _rows:
		var mode := _mode_of(row)
		if mode == "HUMAN" or mode == "BOT":
			players += 1
	_max_alliance.clear()
	var top := maxi(players - 1, 1)  # sizes 1 .. players-1; 1 = no alliances (and no Alliances phase)
	for n in range(1, top + 1):
		_max_alliance.add_item(str(n))
	_max_alliance.disabled = top == 1
	_max_size = clampi(_max_pref, 1, top)
	_max_alliance.select(_max_size - 1)


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
	for i in SEATS:
		var row: Dictionary = _rows[i]
		for key in ["mode", "faction", "alliance", "strategy", "behavior", "ai"]:
			cfg.set_value("seat%d" % i, key, (row[key] as OptionButton).selected)
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
	for i in SEATS:
		var row: Dictionary = _rows[i]
		for key in ["mode", "faction", "alliance", "strategy", "behavior", "ai"]:
			var o: OptionButton = row[key]
			var idx := int(cfg.get_value("seat%d" % i, key, o.selected))
			if idx >= 0 and idx < o.item_count:
				o.select(idx)
	_loading = false
